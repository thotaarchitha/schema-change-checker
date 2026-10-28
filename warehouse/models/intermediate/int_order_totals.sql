with items as (
    select
        oi.order_id,
        sum(oi.quantity * p.unit_price) as gross_amount
    from {{ ref('stg_order_items') }} as oi
    inner join {{ ref('stg_products') }} as p on oi.product_id = p.product_id
    group by oi.order_id
),

orders as (
    -- discount is a rate (0.10 = 10% off), not an amount; naming it that way here
    select
        order_id,
        customer_id,
        order_date,
        status,
        discount as discount_rate
    from {{ ref('stg_orders') }}
)

select
    o.order_id,
    o.customer_id,
    o.order_date,
    o.status,
    coalesce(i.gross_amount, 0) as gross_amount,
    round(coalesce(i.gross_amount, 0) * o.discount_rate, 2) as discount_amount,
    coalesce(i.gross_amount, 0) - round(coalesce(i.gross_amount, 0) * o.discount_rate, 2) as net_amount
from orders as o
left join items as i on o.order_id = i.order_id
