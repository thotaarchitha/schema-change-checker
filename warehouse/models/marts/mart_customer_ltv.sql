-- LTV here is cash actually collected (payments), not booked revenue,
-- because returns and unpaid orders make booked revenue optimistic.
-- Cut-offs put roughly the bottom and top fifth of the sample data in low and high.
select
    c.customer_id,
    c.email,
    c.signup_date,
    c.order_count,
    coalesce(sum(f.amount_paid), 0) as lifetime_value,
    case
        when coalesce(sum(f.amount_paid), 0) >= 6000 then 'high'
        when coalesce(sum(f.amount_paid), 0) >= 2500 then 'medium'
        else 'low'
    end as value_tier
from {{ ref('dim_customers') }} as c
left join {{ ref('fct_orders') }} as f on c.customer_id = f.customer_id
group by c.customer_id, c.email, c.signup_date, c.order_count
