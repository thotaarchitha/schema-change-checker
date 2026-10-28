with paid as (
    select
        order_id,
        sum(amount) as amount_paid,
        count(*) as payment_count
    from {{ ref('stg_payments') }}
    group by order_id
)

select
    t.order_id,
    t.customer_id,
    t.order_date,
    t.status,
    t.gross_amount,
    t.discount_amount,
    t.net_amount as net_revenue,
    coalesce(p.amount_paid, 0) as amount_paid,
    coalesce(p.payment_count, 0) as payment_count
from {{ ref('int_order_totals') }} as t
left join paid as p on t.order_id = p.order_id
