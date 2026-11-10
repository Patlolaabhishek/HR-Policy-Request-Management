import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, time
import streamlit as st
import pandas as pd

BASE = Path(__file__).resolve().parent
DB = BASE / 'hr.db'
SEED = BASE / 'seed.sql'

st.set_page_config(page_title='HR Policy Request Management', page_icon='🧑‍💼', layout='wide')

@st.cache_resource
def db_connection():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='Ticket'").fetchone():
        if not SEED.exists():
            st.error('Database and seed.sql are missing. Keep seed.sql in this project folder.')
            st.stop()
        conn.executescript(SEED.read_text(encoding='utf-8'))
        conn.commit()
    return conn

conn = db_connection()

def df(query, params=()):
    return pd.read_sql_query(query, conn, params=params)

def add_business_hours(start, hours):
    current = start
    remaining = float(hours)
    while remaining > 0:
        if current.weekday() >= 5:
            current = (current + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            continue
        if current.hour < 9:
            current = current.replace(hour=9, minute=0, second=0, microsecond=0)
        if current.hour >= 17:
            current = (current + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            continue
        available = (datetime.combine(current.date(), time(17, 0)) - current).total_seconds()/3600
        used = min(remaining, available)
        current += timedelta(hours=used)
        remaining -= used
        if remaining > 0:
            current = (current + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return current

def next_id(prefix, table, column):
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] + 1
    return f'{prefix}-{n:06d}'

st.title('🧑‍💼 HR Policy Request Management')
st.caption('A working demonstration for employee HR requests, queue routing, policy references, and SLA monitoring.')

# Dashboard metrics
m1,m2,m3,m4 = st.columns(4)
total = conn.execute('SELECT COUNT(*) FROM Ticket').fetchone()[0]
open_count = conn.execute("SELECT COUNT(*) FROM Ticket WHERE status <> 'Resolved'").fetchone()[0]
breached = conn.execute("SELECT COUNT(*) FROM SLA WHERE sla_status='Breached'").fetchone()[0]
sensitive = conn.execute('SELECT COUNT(*) FROM Ticket WHERE is_sensitive=1').fetchone()[0]
m1.metric('Total requests', f'{total:,}')
m2.metric('Active requests', f'{open_count:,}')
m3.metric('SLA breaches', f'{breached:,}')
m4.metric('Sensitive requests', f'{sensitive:,}')

pages = st.tabs(['Dashboard', 'Submit a request', 'Track requests', 'Policy library', 'Data checks'])
with pages[0]:
    left, right = st.columns(2)
    with left:
        st.subheader('Requests by status')
        st.bar_chart(df('SELECT status, COUNT(*) AS requests FROM Ticket GROUP BY status').set_index('status'))
    with right:
        st.subheader('Requests by category')
        st.bar_chart(df('SELECT c.category_name, COUNT(*) AS requests FROM Ticket t JOIN Category c USING(category_id) GROUP BY c.category_name').set_index('category_name'))
    st.subheader('Latest requests')
    st.dataframe(df("SELECT t.ticket_id, t.subject, t.status, t.priority, t.current_queue, t.created_at FROM Ticket t ORDER BY t.created_at DESC LIMIT 12"), use_container_width=True, hide_index=True)

with pages[1]:
    st.subheader('Create an HR policy request')
    employees = df("SELECT employee_id, first_name || ' ' || last_name AS employee_name, department_id FROM Employee WHERE is_active=1 ORDER BY employee_name")
    categories = df('SELECT category_id, category_name, default_queue, default_sla_hours, sensitive_by_default FROM Category ORDER BY category_name')
    with st.form('new_ticket_form', clear_on_submit=True):
        employee_label = st.selectbox('Employee', employees.apply(lambda r: f"{r.employee_name} ({r.employee_id})", axis=1).tolist())
        employee_id = employee_label.rsplit('(',1)[1].rstrip(')')
        emp = employees[employees.employee_id == employee_id].iloc[0]
        category_label = st.selectbox('Request category', categories.apply(lambda r: f"{r.category_name} ({r.category_id})", axis=1).tolist())
        category_id = category_label.rsplit('(',1)[1].rstrip(')')
        category = categories[categories.category_id == category_id].iloc[0]
        subject = st.text_input('Subject', max_chars=160)
        description = st.text_area('Describe your request', height=120)
        priority = st.selectbox('Priority', ['Low','Normal','High','Urgent'], index=1)
        sensitive = st.checkbox('This request contains sensitive or confidential information', value=bool(category.sensitive_by_default))
        audience = st.selectbox('Policy audience', ['All Employees','Full-time Employees','Part-time Employees','Managers','New Hires'])
        submitted = st.form_submit_button('Submit request', type='primary')
    if submitted:
        if not subject.strip() or not description.strip():
            st.error('Please enter both a subject and request description.')
        else:
            now = datetime.now().replace(microsecond=0)
            ticket_id = next_id('TKT-APP', 'Ticket', 'ticket_id')
            queue = 'HR-Employee-Relations' if sensitive else category.default_queue
            sla_due = add_business_hours(now, int(category.default_sla_hours))
            policy = conn.execute('SELECT policy_id FROM PolicyDocument WHERE category_id=? ORDER BY policy_id LIMIT 1',(category_id,)).fetchone()
            policy_id = policy['policy_id'] if policy else conn.execute('SELECT policy_id FROM PolicyDocument ORDER BY policy_id LIMIT 1').fetchone()[0]
            conn.execute('''INSERT INTO Ticket(ticket_id,employee_id,department_id,category_id,subject,description,created_at,status,priority,is_sensitive,ambiguity_flag,missing_context_flag,intended_queue,current_queue,policy_id,requested_audience,closed_at,issue_type) VALUES(?,?,?,?,?,?,?,'Open',?,?,0,0,?,?,?,?,NULL,'Live application request')''',
                (ticket_id,employee_id,emp.department_id,category_id,subject.strip(),description.strip(),now.isoformat(sep=' '),priority,int(sensitive),queue,queue,policy_id,audience))
            assignment_id = next_id('ASN-APP','Assignment','assignment_id')
            conn.execute('INSERT INTO Assignment VALUES(?,?,?,?,?,?,1)',(assignment_id,ticket_id,queue,None,now.isoformat(sep=' '),'Automatic category/sensitivity routing'))
            sla_id = next_id('SLA-APP','SLA','sla_id')
            conn.execute('INSERT INTO SLA VALUES(?,?,?,?,?,NULL,?,?,?,?)',(sla_id,ticket_id,int(category.default_sla_hours),now.isoformat(sep=' '),sla_due.isoformat(sep=' '),'Open','Weekday 09:00-17:00','Monday-Friday 09:00-17:00','Live application request'))
            event_id = next_id('EVT-APP','TicketEvent','event_id')
            conn.execute('INSERT INTO TicketEvent VALUES(?,?,?,?,?,?,?)',(event_id,ticket_id,'Created',now.isoformat(sep=' '),None,'Open',employee_id))
            conn.commit()
            st.success(f'Request submitted. Your ticket number is {ticket_id}. It has been routed to {queue}. SLA due: {sla_due:%d %b %Y, %I:%M %p}.')

with pages[2]:
    st.subheader('Track HR requests')
    search = st.text_input('Search by ticket number, employee ID, subject, or queue')
    query = '''SELECT t.ticket_id, t.subject, t.status, t.priority, t.current_queue, t.created_at, t.is_sensitive, s.due_at, s.sla_status
               FROM Ticket t LEFT JOIN SLA s USING(ticket_id)'''
    result = df(query + ' ORDER BY t.created_at DESC LIMIT 500')
    if search.strip():
        mask = result.astype(str).apply(lambda col: col.str.contains(search.strip(),case=False,na=False)).any(axis=1)
        result = result[mask]
    st.dataframe(result, use_container_width=True, hide_index=True)
    st.caption('Sensitive requests are routed to the restricted Employee Relations queue at submission.')

with pages[3]:
    st.subheader('Search policy library')
    term = st.text_input('Search policy title or summary')
    policies = df('''SELECT p.policy_id,p.title,p.audience,p.confidentiality,pv.version_number,pv.effective_from,pv.effective_to,pv.status,pv.content_summary
                     FROM PolicyDocument p JOIN PolicyVersion pv USING(policy_id)
                     ORDER BY p.title,pv.version_number DESC''')
    if term.strip():
        policies = policies[policies.astype(str).apply(lambda c:c.str.contains(term,case=False,na=False)).any(axis=1)]
    st.dataframe(policies, use_container_width=True, hide_index=True)
    st.info('For a policy answer, verify the audience and effective dates before relying on a version. This demo displays policy summaries; it does not generate legal or HR eligibility decisions.')

with pages[4]:
    st.subheader('Database integrity checks')
    fk = conn.execute('PRAGMA foreign_key_check').fetchall()
    st.metric('Foreign-key violations', len(fk))
    checks = {
        'Messages without a ticket': "SELECT COUNT(*) FROM Message m LEFT JOIN Ticket t USING(ticket_id) WHERE t.ticket_id IS NULL",
        'Tickets without an employee': "SELECT COUNT(*) FROM Ticket t LEFT JOIN Employee e USING(employee_id) WHERE e.employee_id IS NULL",
        'Tickets without an SLA': "SELECT COUNT(*) FROM Ticket t LEFT JOIN SLA s USING(ticket_id) WHERE s.ticket_id IS NULL",
        'Duplicate current policy versions': "SELECT COUNT(*) FROM (SELECT policy_id FROM PolicyVersion WHERE status='Current' GROUP BY policy_id HAVING COUNT(*)>1)"
    }
    rows=[]
    for label, sql in checks.items(): rows.append({'Check':label,'Issues found':conn.execute(sql).fetchone()[0]})
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    st.caption('The database includes synthetic historical records. New submissions are added to the same SQLite database.')

st.divider()
st.caption('Synthetic demonstration data only • Not connected to a live HR/payroll system • Configure access controls before any real deployment.')
