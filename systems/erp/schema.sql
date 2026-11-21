-- Brewline mini ERP (SQLite). Rebuilt from seed.sql; never edited by hand.
PRAGMA foreign_keys = ON;

CREATE TABLE vendors (
  vendor_id   TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  country     TEXT NOT NULL
);

CREATE TABLE parts (
  part_id     TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  hts_code    TEXT NOT NULL,
  unit_cost   REAL NOT NULL
);

CREATE TABLE customers (
  customer_id TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  country     TEXT NOT NULL,
  contract_id TEXT NOT NULL
);

CREATE TABLE contracts (
  contract_id        TEXT PRIMARY KEY,
  customer_id        TEXT NOT NULL REFERENCES customers(customer_id),
  effective_date     TEXT NOT NULL,
  template           TEXT NOT NULL,          -- 'direct' | 'distributor'
  file_path          TEXT NOT NULL
);

CREATE TABLE purchase_orders (
  po_id       TEXT PRIMARY KEY,
  vendor_id   TEXT NOT NULL REFERENCES vendors(vendor_id),
  part_id     TEXT NOT NULL REFERENCES parts(part_id),
  qty         INTEGER NOT NULL,
  unit_cost   REAL NOT NULL,
  order_date  TEXT NOT NULL,
  incoterm    TEXT NOT NULL
);

CREATE TABLE shipments (
  shipment_id    TEXT PRIMARY KEY,
  po_id          TEXT NOT NULL REFERENCES purchase_orders(po_id),
  vendor_id      TEXT NOT NULL REFERENCES vendors(vendor_id),
  part_id        TEXT NOT NULL REFERENCES parts(part_id),
  qty            INTEGER NOT NULL,
  origin_country TEXT NOT NULL,
  entry_no       TEXT NOT NULL UNIQUE,       -- CBP entry number; crosswalk key to customs
  entry_date     TEXT NOT NULL,
  received_date  TEXT NOT NULL,
  customs_value  REAL NOT NULL
);

CREATE TABLE inventory_layers (
  layer_id     INTEGER PRIMARY KEY,
  shipment_id  TEXT NOT NULL REFERENCES shipments(shipment_id),
  part_id      TEXT NOT NULL REFERENCES parts(part_id),
  qty_received INTEGER NOT NULL,
  qty_on_hand  INTEGER NOT NULL,
  unit_cost    REAL NOT NULL,
  duty_per_unit REAL NOT NULL DEFAULT 0  -- CBP 7501 duty (all programs) / qty_received
);

CREATE TABLE machines (
  machine_id    TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  list_price    REAL NOT NULL,
  surcharge_pct REAL NOT NULL
);

CREATE TABLE bom (
  machine_id TEXT NOT NULL REFERENCES machines(machine_id),
  part_id    TEXT NOT NULL REFERENCES parts(part_id),
  qty        INTEGER NOT NULL,
  PRIMARY KEY (machine_id, part_id)
);

CREATE TABLE sales_orders (
  so_id           TEXT PRIMARY KEY,
  customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
  invoice_id      TEXT NOT NULL UNIQUE,      -- Brewline invoice number; Stripe id lives in the desk crosswalk
  invoice_date    TEXT NOT NULL,
  paid_date       TEXT,
  machine_total   REAL NOT NULL,
  surcharge_total REAL NOT NULL
);

CREATE TABLE sales_order_lines (
  so_id      TEXT NOT NULL REFERENCES sales_orders(so_id),
  machine_id TEXT NOT NULL REFERENCES machines(machine_id),
  qty        INTEGER NOT NULL,
  unit_price REAL NOT NULL
);

CREATE TABLE consumption (                     -- FIFO parts consumed per sales order
  so_id    TEXT NOT NULL REFERENCES sales_orders(so_id),
  layer_id INTEGER NOT NULL REFERENCES inventory_layers(layer_id),
  part_id  TEXT NOT NULL REFERENCES parts(part_id),
  qty      INTEGER NOT NULL
);
