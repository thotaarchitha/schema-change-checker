select
    customer_id,
    count(*) as order_count,
    min(order_date) as first_order_date,
    max(order_date) as last_order_date
from {{ ref('stg_orders') }}
where status <> 'cancelled'
group by customer_id
