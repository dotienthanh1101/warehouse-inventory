"""Only authenticated identity fields belong in inventory provenance; never tokens."""
from backend.app.warehouse_scope import current_warehouse_id


def actor_snapshot(identity):
    if (not isinstance(identity, dict) or identity.get('role') != 'user'
            or not identity.get('user_id')
            or identity.get('warehouse_id') != current_warehouse_id()):
        raise ValueError('Không có người xác nhận hợp lệ trong kho hiện tại.')
    return {key: identity.get(key) for key in ('user_id', 'username', 'display_name', 'warehouse_id')}
