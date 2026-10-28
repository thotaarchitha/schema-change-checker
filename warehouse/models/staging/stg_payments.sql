select
    id as payment_id,
    order_id,
    method as payment_method,
    cast(amount / 100.0 as numeric(12, 2)) as amount
from {{ ref('payments') }}
