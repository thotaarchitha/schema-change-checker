select
    id as order_item_id,
    order_id,
    product_id,
    quantity
from {{ ref('order_items') }}
where quantity > 0
