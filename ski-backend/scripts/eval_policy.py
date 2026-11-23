"""回答级统一验收：安全兜底不等于成功回答。"""

def passed(row):
    if not (row.get('answered') and row.get('citation_valid') and row.get('grounded')
            and not row.get('degraded') and not row.get('error') and row.get('hit') is not False):
        return False
    kind = row.get('kind', '')
    if kind == 'refusal' or kind.endswith('_missing'):
        return bool(row.get('refused'))
    return bool(row.get('cited') and
                (not row.get('price_question') or row.get('stale_notice')))
