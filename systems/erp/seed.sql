-- Brewline mini ERP seed: tiny world v1. Derived from data/truth/world.json.

INSERT INTO vendors VALUES
  ('VEND-001', 'Shenzhen Boardworks Ltd', 'CN'),
  ('VEND-002', 'Ningbo Steelform Co',     'CN'),
  ('VEND-003', 'Milano Grind S.r.l.',     'IT');

INSERT INTO parts VALUES
  ('PRT-BOARD', 'Electronic controller board', '8537.10.91', 200.00),
  ('PRT-FRAME', 'Fabricated steel frame',      '7326.90.86', 300.00),
  ('PRT-BURR',  'Grinder burr set',            '8509.90.55',  80.00);

INSERT INTO customers VALUES
  ('CUST-001', 'Bean Street Coffee Co.', 'US', 'CON-001'),
  ('CUST-002', 'Harbor Roasters',        'US', 'CON-002'),
  ('CUST-003', 'Northwind Distribution', 'US', 'CON-003'),
  ('CUST-004', 'Cascade Coffee Supply',  'US', 'CON-004');

INSERT INTO contracts VALUES
  ('CON-001', 'CUST-001', '2025-01-10', 'direct',      'data/documents/contracts/CON-001-bean-street.md'),
  ('CON-002', 'CUST-002', '2025-01-20', 'distributor', 'data/documents/contracts/CON-002-harbor.md'),
  ('CON-003', 'CUST-003', '2025-02-03', 'distributor', 'data/documents/contracts/CON-003-northwind.md'),
  ('CON-004', 'CUST-004', '2025-02-17', 'distributor', 'data/documents/contracts/CON-004-cascade.md');

INSERT INTO purchase_orders VALUES
  ('PO-2501', 'VEND-001', 'PRT-BOARD', 100, 200.00, '2024-12-10', 'FOB'),
  ('PO-2502', 'VEND-002', 'PRT-FRAME',  40, 300.00, '2024-12-18', 'FOB'),
  ('PO-2503', 'VEND-003', 'PRT-BURR',  100,  80.00, '2025-01-08', 'FOB'),
  ('PO-2504', 'VEND-001', 'PRT-BOARD',  75, 200.00, '2025-01-22', 'FOB'),
  ('PO-2505', 'VEND-002', 'PRT-FRAME',  30, 300.00, '2025-02-05', 'FOB'),
  ('PO-2506', 'VEND-003', 'PRT-BURR',  125,  80.00, '2025-02-19', 'FOB'),
  ('PO-2507', 'VEND-001', 'PRT-BOARD',  90, 200.00, '2025-03-12', 'FOB'),
  ('PO-2508', 'VEND-002', 'PRT-FRAME',  36, 300.00, '2025-04-02', 'FOB'),
  ('PO-2509', 'VEND-003', 'PRT-BURR',   90,  80.00, '2025-04-23', 'FOB'),
  ('PO-2510', 'VEND-001', 'PRT-BOARD', 110, 200.00, '2025-05-14', 'FOB');

INSERT INTO shipments VALUES
  ('SHP-001', 'PO-2501', 'VEND-001', 'PRT-BOARD', 100, 'CN', 'BRK-2500101-1', '2025-01-14', '2025-01-17', 20000.00),
  ('SHP-002', 'PO-2502', 'VEND-002', 'PRT-FRAME',  40, 'CN', 'BRK-2500128-2', '2025-01-28', '2025-01-31', 12000.00),
  ('SHP-003', 'PO-2503', 'VEND-003', 'PRT-BURR',  100, 'IT', 'BRK-2500211-3', '2025-02-11', '2025-02-14',  8000.00),
  ('SHP-004', 'PO-2504', 'VEND-001', 'PRT-BOARD',  75, 'CN', 'BRK-2500225-4', '2025-02-25', '2025-02-28', 15000.00),
  ('SHP-005', 'PO-2505', 'VEND-002', 'PRT-FRAME',  30, 'CN', 'BRK-2500311-5', '2025-03-11', '2025-03-14',  9000.00),
  ('SHP-006', 'PO-2506', 'VEND-003', 'PRT-BURR',  125, 'IT', 'BRK-2500325-6', '2025-03-25', '2025-03-28', 10000.00),
  ('SHP-007', 'PO-2507', 'VEND-001', 'PRT-BOARD',  90, 'CN', 'BRK-2500415-7', '2025-04-15', '2025-04-18', 18000.00),
  ('SHP-008', 'PO-2508', 'VEND-002', 'PRT-FRAME',  36, 'CN', 'BRK-2500506-8', '2025-05-06', '2025-05-09', 10800.00),
  ('SHP-009', 'PO-2509', 'VEND-003', 'PRT-BURR',   90, 'IT', 'BRK-2500527-9', '2025-05-27', '2025-05-30',  7200.00),
  ('SHP-010', 'PO-2510', 'VEND-001', 'PRT-BOARD', 110, 'CN', 'BRK-2500617-0', '2025-06-17', '2025-06-20', 22000.00);

-- FIFO layers; on-hand after 2025 sales (12 boards, 3 frames, 9 burrs consumed).
-- duty_per_unit = CBP 7501 duty (all programs) / qty_received: boards 90.00, frames 210.00, burrs 12.00.
INSERT INTO inventory_layers VALUES
  ( 1, 'SHP-001', 'PRT-BOARD', 100,  88, 200.00, 90.00),
  ( 2, 'SHP-002', 'PRT-FRAME',  40,  37, 300.00, 210.00),
  ( 3, 'SHP-003', 'PRT-BURR',  100,  91,  80.00, 12.00),
  ( 4, 'SHP-004', 'PRT-BOARD',  75,  75, 200.00, 90.00),
  ( 5, 'SHP-005', 'PRT-FRAME',  30,  30, 300.00, 210.00),
  ( 6, 'SHP-006', 'PRT-BURR',  125, 125,  80.00, 12.00),
  ( 7, 'SHP-007', 'PRT-BOARD',  90,  90, 200.00, 90.00),
  ( 8, 'SHP-008', 'PRT-FRAME',  36,  36, 300.00, 210.00),
  ( 9, 'SHP-009', 'PRT-BURR',   90,  90,  80.00, 12.00),
  (10, 'SHP-010', 'PRT-BOARD', 110, 110, 200.00, 90.00);

INSERT INTO machines VALUES
  ('MCH-ESP1', 'Brewline Espresso X1', 4800.00, 12.0),
  ('MCH-GRD1', 'Brewline Grinder Pro', 1250.00,  9.6);

INSERT INTO bom VALUES
  ('MCH-ESP1', 'PRT-BOARD', 2),
  ('MCH-ESP1', 'PRT-FRAME', 1),
  ('MCH-ESP1', 'PRT-BURR',  1),
  ('MCH-GRD1', 'PRT-BOARD', 1),
  ('MCH-GRD1', 'PRT-BURR',  1);

INSERT INTO sales_orders VALUES
  ('SO-1001', 'CUST-001', 'INV-1001', '2025-03-05', '2025-04-01', 9600.00, 1152.00),
  ('SO-1002', 'CUST-001', 'INV-1002', '2025-04-02', '2025-04-30', 5000.00,  480.00),
  ('SO-1003', 'CUST-002', 'INV-1003', '2025-04-22', '2025-05-20', 4800.00,  576.00),
  ('SO-1004', 'CUST-003', 'INV-1004', '2025-05-14', '2025-06-12', 2500.00,  240.00);

INSERT INTO sales_order_lines VALUES
  ('SO-1001', 'MCH-ESP1', 2, 4800.00),
  ('SO-1002', 'MCH-GRD1', 4, 1250.00),
  ('SO-1003', 'MCH-ESP1', 1, 4800.00),
  ('SO-1004', 'MCH-GRD1', 2, 1250.00);

INSERT INTO consumption VALUES
  ('SO-1001', 1, 'PRT-BOARD', 4), ('SO-1001', 2, 'PRT-FRAME', 2), ('SO-1001', 3, 'PRT-BURR', 2),
  ('SO-1002', 1, 'PRT-BOARD', 4), ('SO-1002', 3, 'PRT-BURR', 4),
  ('SO-1003', 1, 'PRT-BOARD', 2), ('SO-1003', 2, 'PRT-FRAME', 1), ('SO-1003', 3, 'PRT-BURR', 1),
  ('SO-1004', 1, 'PRT-BOARD', 2), ('SO-1004', 3, 'PRT-BURR', 2);
