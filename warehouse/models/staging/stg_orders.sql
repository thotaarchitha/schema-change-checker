-- The source system has written every status spelling it could think of.
select
    id as order_id,
    customer_id,
    order_date,
    case
        when lower(trim(status)) in ('completed', 'complete') then 'completed'
        when lower(trim(status)) in ('cancelled', 'canceled') then 'cancelled'
        else lower(trim(status))
    end as status
from {{ ref('orders') }}
