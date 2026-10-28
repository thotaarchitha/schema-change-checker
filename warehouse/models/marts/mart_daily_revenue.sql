-- Net revenue is spread across an order's lines by their share of gross,
-- so a discounted order doesn't make one category look better than it is.
with lines as (
    select
        f.order_date,
        dp.category,
        oi.quantity * dp.unit_price as line_gross,
        f.gross_amount,
        f.net_revenue
    from {{ ref('fct_orders') }} as f
    inner join {{ ref('stg_order_items') }} as oi on f.order_id = oi.order_id
    inner join {{ ref('dim_products') }} as dp on oi.product_id = dp.product_id
    where f.status in ('completed', 'shipped')
)

select
    order_date as revenue_date,
    category,
    count(*) as line_count,
    sum(line_gross) as gross_revenue,
    round(sum(case when gross_amount > 0 then net_revenue * line_gross / gross_amount else 0 end), 2) as net_revenue
from lines
group by order_date, category
