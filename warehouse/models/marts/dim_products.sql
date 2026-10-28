-- select * on purpose: whatever staging exposes, the dimension exposes.
-- Convenient, and exactly the kind of thing that hides a dropped column.
select
    *,
    case
        when unit_price >= 200 then 'premium'
        when unit_price >= 50 then 'mid'
        else 'budget'
    end as price_tier
from {{ ref('stg_products') }}
