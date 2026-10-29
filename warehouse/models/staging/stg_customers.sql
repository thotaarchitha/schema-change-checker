select
    id as customer_id,
    first_name,
    last_name,
    lower(email) as email_address,
    city,
    signup_date
from {{ ref('customers') }}
