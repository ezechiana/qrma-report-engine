"""Read-only compatibility check for legacy on-demand table helpers."""
from sqlalchemy import text


def existing_objects(db, names):
    if db.get_bind().dialect.name != 'postgresql':
        return False
    return bool(db.scalar(text('SELECT bool_and(to_regclass(n) IS NOT NULL) FROM unnest(CAST(:names AS text[])) AS objects(n)'),
                          {'names': list(names)}))
