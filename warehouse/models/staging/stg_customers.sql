select
    id as customer_id,
    first_name,
    last_name,
    first_name || ' ' || last_name as full_name,
    lower(email) as email,
    city,
    signup_date
from {{ ref('customers') }}
