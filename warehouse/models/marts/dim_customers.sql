select
    c.customer_id,
    c.first_name,
    c.last_name,
    c.email_address as email,
    c.city,
    c.signup_date,
    coalesce(co.order_count, 0) as order_count,
    co.first_order_date,
    co.last_order_date
from {{ ref('stg_customers') }} as c
left join {{ ref('int_customer_orders') }} as co on c.customer_id = co.customer_id
