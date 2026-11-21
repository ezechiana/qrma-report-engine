"""Explicit operations step; never creates login credentials or alters legacy ACLs."""
import argparse
from sqlalchemy import text


def provision(engine, *, schema, owner, writer, reviewer):
    if len({owner, writer, reviewer}) != 3:
        raise ValueError('Three distinct database roles are required')
    q = engine.dialect.identifier_preparer.quote
    with engine.begin() as c:
        for role in (owner, writer, reviewer):
            row = c.execute(text('SELECT rolcanlogin,rolsuper,rolcreaterole,rolcreatedb,rolbypassrls FROM pg_roles WHERE rolname=:r'), {'r': role}).first()
            if not row or any(row[1:]) or (role == owner and row[0]) or (role != owner and not row[0]):
                raise ValueError('Provision independently controlled non-privileged roles first')
        for role in (writer, reviewer):
            if c.scalar(text('SELECT pg_has_role(:r,:o,\'MEMBER\')'), {'r':role,'o':owner}):
                raise ValueError('Runtime/reviewer must not inherit ownership')
            if c.scalar(text('SELECT has_schema_privilege(:r,:s,\'CREATE\')'), {'r':role,'s':schema}):
                raise ValueError('Runtime/reviewer must not create schema objects')
        if c.scalar(text('SELECT pg_has_role(:w,:r,\'MEMBER\')'), {'w':writer,'r':reviewer}):
            raise ValueError('Runtime must not assume reviewer role')
        if c.scalar(text('''SELECT EXISTS(SELECT 1 FROM pg_roles
            WHERE rolname <> :r AND pg_has_role(:r,oid,'MEMBER'))'''), {'r':reviewer}):
            raise ValueError('Use an isolated reviewer login without inherited roles')
        if c.scalar(text('''SELECT EXISTS(SELECT 1 FROM pg_class t
            JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname=:s AND t.relkind IN ('r','p','v','m','f')
            AND t.relname NOT IN ('clinical_audit_events','clinical_audit_reviewer_scopes')
            AND (has_table_privilege(:r,t.oid,'SELECT') OR has_any_column_privilege(:r,t.oid,'SELECT')))'''),
            {'r':reviewer,'s':schema}):
            raise ValueError('Reviewer must not have unscoped application data access')
        c.exec_driver_sql(f'GRANT USAGE ON SCHEMA {q(schema)} TO {q(owner)}, {q(writer)}, {q(reviewer)}')
        for table in ('clinical_audit_events', 'clinical_audit_reviewer_scopes'):
            name = f'{q(schema)}.{q(table)}'
            c.exec_driver_sql(f'ALTER TABLE {name} OWNER TO {q(owner)}')
            c.exec_driver_sql(f'REVOKE ALL ON {name} FROM PUBLIC, {q(writer)}, {q(reviewer)}')
            c.exec_driver_sql(f'GRANT SELECT ON {name} TO {q(reviewer)}')
        c.exec_driver_sql(f'ALTER FUNCTION {q(schema)}.clinical_audit_refuse_mutation() OWNER TO {q(owner)}')
        c.exec_driver_sql(f'GRANT INSERT ON {q(schema)}.clinical_audit_events TO {q(writer)}')
        check_runtime(c, role=writer, schema=schema)
        for privilege in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'TRIGGER'):
            if c.scalar(text('SELECT has_table_privilege(:r,:t,:p)'),
                        {'r':reviewer,'t':f'{schema}.clinical_audit_events','p':privilege}):
                raise ValueError('Reviewer must be read only, including inherited privileges')
        for privilege in ('INSERT', 'UPDATE'):
            if c.scalar(text('SELECT has_any_column_privilege(:r,:t,:p)'),
                        {'r':reviewer,'t':f'{schema}.clinical_audit_events','p':privilege}):
                raise ValueError('Reviewer must not have column write privileges')


def check_runtime(c, *, role=None, schema=None):
    role = role or c.scalar(text('SELECT current_user'))
    schema = schema or c.scalar(text('SELECT current_schema()'))
    table = f'{schema}.clinical_audit_events'
    flags = c.execute(text('SELECT rolsuper,rolcreaterole,rolcreatedb,rolbypassrls FROM pg_roles WHERE rolname=:r'), {'r':role}).one()
    if any(flags) or c.scalar(text("SELECT has_schema_privilege(:r,:s,'CREATE')"), {'r':role,'s':schema}):
        raise RuntimeError('Clinical audit runtime role is privileged')
    if c.scalar(text('''SELECT EXISTS(SELECT 1 FROM pg_roles
        WHERE (rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls)
        AND pg_has_role(:r,oid,'MEMBER'))'''), {'r':role}):
        raise RuntimeError('Clinical audit runtime can assume a privileged role')
    for privilege in ('SELECT','UPDATE','DELETE','TRUNCATE','TRIGGER','REFERENCES'):
        if c.scalar(text('SELECT has_table_privilege(:r,:t,:p)'), {'r':role,'t':table,'p':privilege}):
            raise RuntimeError('Clinical audit runtime privileges are unsafe')
    for privilege in ('SELECT', 'UPDATE', 'REFERENCES'):
        if c.scalar(text('SELECT has_any_column_privilege(:r,:t,:p)'), {'r':role,'t':table,'p':privilege}):
            raise RuntimeError('Clinical audit runtime column privileges are unsafe')
    if not c.scalar(text("SELECT has_table_privilege(:r,:t,'INSERT')"), {'r':role,'t':table}):
        raise RuntimeError('Clinical audit append permission missing')
    if c.scalar(text("SELECT has_table_privilege(:r,:t,'INSERT WITH GRANT OPTION')"), {'r':role,'t':table}):
        raise RuntimeError('Clinical audit writer must not delegate append privileges')
    if c.scalar(text("SELECT pg_has_role(:r,relowner,'MEMBER') FROM pg_class WHERE oid=to_regclass(:t)"), {'r':role,'t':table}):
        raise RuntimeError('Clinical audit runtime inherits table ownership')
    state = c.execute(text('SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:t)'), {'t':table}).one()
    if not all(state):
        raise RuntimeError('Clinical audit row security missing')
    if c.scalar(text('''SELECT count(*) FROM pg_trigger WHERE tgrelid=to_regclass(:t)
        AND tgname IN ('clinical_audit_immutable','clinical_audit_no_truncate')
        AND tgenabled IN ('O','A') AND NOT tgisinternal'''), {'t':table}) != 2:
        raise RuntimeError('Clinical audit mutation safeguards missing')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--writer', required=True)
    parser.add_argument('--reviewer', required=True)
    args = parser.parse_args()
    from app.db.session import engine
    provision(engine, **vars(args))
    print('Clinical audit roles provisioned and runtime privileges checked')


if __name__ == '__main__':
    main()
