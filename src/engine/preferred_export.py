"""Read-only preferred supplier export; shares the decision engine's ranking logic."""
import csv
import io
import re
from datetime import date
from decimal import localcontext

from .core import Engine, money
from .policy import select

COLUMNS = ['item_code', 'rank', 'supplier_id', 'reference_price']


def render(data, as_of, policy_version=None):
    if not isinstance(as_of, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', as_of):
        raise ValueError('Supply an as-of date in YYYY-MM-DD format')
    try:
        day = date.fromisoformat(as_of)
    except ValueError:
        raise ValueError('Supply a valid as-of date') from None
    policies = data['policies']
    if policy_version:
        index = next((i for i, p in enumerate(policies) if p['version'] == policy_version), None)
        if index is None:
            raise ValueError('Unknown approved policy version')
        policies = policies[:index + 1]
    policy = select(policies, day)
    engine = Engine(data, policies)
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=COLUMNS)
    writer.writeheader()
    with localcontext() as context:
        context.prec = 28
        for item in sorted(engine.items):
            for rank, supplier in enumerate(engine.preferred(item, day, policy)[:3], 1):
                writer.writerow(dict(item_code=item, rank=rank, supplier_id=supplier['supplier_id'],
                                     reference_price=money(supplier['reference_price'])))
    return out.getvalue()
