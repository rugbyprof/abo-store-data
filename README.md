# ABO Store Data

A mock-store product catalog built from the **Amazon Berkeley Objects (ABO)**
dataset: about 5,000 real products with real names, brands, bullet points,
keywords and photos. They are packaged for Postgres (`\copy`-able CSVs) and
MongoDB (NDJSON), with synthetic prices, stock, customers and orders added.

> **Synthetic data notice.** ABO has no prices, inventory, customers or
> orders. Every `price_cents`, `stock`, `created_at`, customer, order and
> order item in this bundle is **generated** (seeded, so everyone gets the
> same values). The prices are plausible, but they are not real Amazon prices,
> and the customer names and `@example.com` addresses don't belong to real people.

## What's in the bundle (`dist/`)

| Path | Contents |
| --- | --- |
| `schema.sql` | Postgres DDL: `categories`, `products`, `product_images`, `customers`, `orders`, `order_items` |
| `load_postgres.sql` | Runs `schema.sql` and then `\copy`s every CSV in one transaction |
| `seed/*.csv` | One CSV per table, with a header row, in Postgres `FORMAT csv` |
| `mongo/products.ndjson` | One full product document per line: embedded category, images and attributes |
| `images/xx/yyyyyyyy.jpg` | Only the images these products use (at most 256 px on the longest side) |
| `LICENSE-CC-BY-4.0.txt` | The dataset license |

Default counts: 357 categories (15 departments plus 342 product types),
5,000 products, about 18,000 product images, 2,000 customers, 8,000 orders
and about 13,500 order items. The bundle is about 170 MB, and nearly all of
that is images (`--max-images 1` brings it down to about 50 MB).

### Loading

```bash
cd dist
createdb store
psql -d store -f load_postgres.sql
mongoimport --db store --collection products --drop --file mongo/products.ndjson
```

Image paths in both stores are relative to the bundle root (for example
`images/be/beaed26f.jpg`), so serve `dist/images/` as static files (in FastAPI,
use `app.mount("/images", StaticFiles(directory="images"))`) and use `path` as
the URL.

### Data model notes

- `categories` is a two-level tree. Departments have `parent_id IS NULL`.
  Leaf rows hold the ABO `product_type` (for example `SOFA` or `COFFEE`), and
  every product points at a leaf.
- `products.bullets` and `products.keywords` are `jsonb` arrays.
  `description` is the bullets joined by newlines, which is handy for
  `to_tsvector` full-text search.
- `products.node_path` is the Amazon browse path when ABO had one (for example
  `Categories/Sports & Fitness/Golf/Golf Cart Accessories`; about 80% of products have one).
- `product_images.orig_width/orig_height` are the size of the **original**
  photo. The bundled files are downscaled. Exactly one image per product has
  `is_main = true`.
- `orders.total_cents` is denormalized and always equals
  `SUM(quantity * unit_price_cents)` over the order's items. That makes it a
  good trigger or consistency exercise.
- Product popularity in orders follows a long tail, so "top sellers" and
  "customers who bought X" queries give interesting answers. No order predates
  its products (products 2023-01 to 2024-06, orders 2024-07 to 2025-12).
- In Mongo, `_id` is the ABO `item_id`, and `product_id` matches Postgres.
  Sparse, type-specific fields (material, style, dimensions, model number...)
  live only in the Mongo `attributes` sub-document. That makes it a natural
  "why use a document store" example.

## Rebuilding

The bundle comes from `build_catalog.py` (Python 3, standard library only).
The script only reads the ABO folder; it never changes it.

```bash
python3 build_catalog.py                          # 5,000 products -> ./dist
python3 build_catalog.py --products 1000 --out small --max-images 1
python3 build_catalog.py --help
```

How it selects products:

1. It reads all 147,702 ABO listings and keeps those with an English
   (`en_US` preferred, then other `en_*`) name and bullet points, a brand
   and a main image. That leaves 113,181.
2. It de-duplicates on `item_id` and keeps the best listing per item
   (English US first, then amazon.com, then the one with the most images).
   That leaves 112,723 candidates.
3. It samples in a fixed hash order derived from `--seed`, capping
   `CELLULAR_PHONE_CASE` (44% of ABO) at `--phone-case-share` (default 3%)
   and every other product type at `--max-type-share` (default 6%).
4. It generates the synthetic fields from `hash(seed, item_id)`. Changing
   `--products` doesn't change an existing product's price or stock, and
   changing `--seed` gives a different, equally reproducible dataset.

The same command with the same seed produces byte-identical CSVs.

## Attribution and license

The product data and images come from **Amazon Berkeley Objects**
(<https://amazon-berkeley-objects.s3.us-east-1.amazonaws.com/index.html>),
© Amazon.com, licensed under the
[Creative Commons Attribution 4.0 International License](https://creativecommons.org/licenses/by/4.0/)
(see `LICENSE-CC-BY-4.0.txt`).

Credit for the data, including all images and 3d models, must be given to:

> Amazon.com

Credit for building the dataset, archives and benchmark sets must be given to:

> Matthieu Guillaumin (Amazon.com), Thomas Dideriksen (Amazon.com),
> Kenan Deng (Amazon.com), Himanshu Arora (Amazon.com),
> Jasmine Collins (UC Berkeley) and Jitendra Malik (UC Berkeley)

**Changes made:** We selected a subset of listings and kept only the English
text fields. We flattened them into relational tables and NDJSON documents,
mapped product types into store departments and copied the downscaled
("small") images. We added synthetic prices, stock levels, dates, customers
and orders that aren't part of ABO. This use isn't endorsed by Amazon.com or
the dataset authors.
