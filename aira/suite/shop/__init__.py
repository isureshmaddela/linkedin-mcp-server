"""Shop: sell products online (retail, makers and creators).

The main objects are **products** (price, stock, options such as sizes, images, a category), **orders** placed from
the public storefront or entered by hand (items snapshot, totals, payment and fulfilment status), **discount
codes** and **customers** (derived from orders). The storefront is a public page (``storefront``) with a cart and a
checkout; each order has its own public page (``order``) with its status and how to pay. Aira does not take card
payments: customers pay through the business's own payment link or instructions, and the business marks the order
paid, which records the revenue.

Modules: :mod:`.store` (tables and rows), :mod:`.service` (pricing, stock, orders, numbers),
:mod:`.shop_routes` (``/api/shop/*`` and the public pages).
"""
