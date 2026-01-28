# Financial Reports Logic Documentation

## Global Filters
All queries apply these filters:
- `shipmentStatus = 'Complete'`
- `pickWindowFrom LIKE '%/2025%'` (date format is MM/DD/YYYY HH:MM:SS)

## Market Definition
- **Market** = 3-letter airport code extracted from WTCH crossdock pattern `WTCH-{AIRPORT}-{#}`
- Examples: LAX, EWR, SFO, ORD, MIA, DFW, etc.
- 42 unique markets identified

## Market Allocation
A shipment belongs to a market when:
- **Outbound**: `pickLocationName LIKE 'WTCH-{MARKET}-%'` AND `dropLocationName NOT LIKE 'WTCH-%'`
- **Inbound**: `dropLocationName LIKE 'WTCH-{MARKET}-%'` AND `pickLocationName NOT LIKE 'WTCH-%'`

---

## Less Than Truckload (LTL) Logic

### What Constitutes a Shipment
- A shipment = 1 `warpId` where `mainShipment = 'YES'`
- Only count shipments where the mainShipment='YES' row directly touches a WTCH crossdock
- Shipments where mainShipment='YES' doesn't reference WTCH (e.g., `FORTUNE LOGISTICS → Envision prints`) are excluded from market reporting

### Revenue Calculation
Revenue has 3 patterns in the data:
1. **Pattern 1 (72%)**: mainShipment='YES' has revenue, and non-xdock legs sum to the same value
2. **Pattern 2 (14%)**: mainShipment='YES' has $0, revenue is in the non-xdock legs
3. **Pattern 3 (14%)**: mainShipment='YES' has revenue, non-xdock legs have $0

**Formula**:
```
IF mainShipment='YES' revenue > 0:
    Revenue = mainShipment='YES' revenueAllocationNumber
ELSE:
    Revenue = SUM(revenueAllocationNumber) for non-crossdock-to-crossdock legs
```

Or equivalently:
```
Revenue = MAX(mainShipment='YES' revenue, SUM of non-xdock-to-xdock legs revenue)
```

**Note**: Crossdock-to-crossdock legs are identified where `pickLocationName = dropLocationName` AND both contain 'WTCH-'

### Cost Calculation
Cost is NOT duplicated like revenue - it's additive across all rows.

**Formula**:
```
Cost = SUM(costAllocationNumber) for ALL warpIds with the same orderCode
```

### LTL Volume (2025, Complete)
- Total mainShipment='YES': 92,365
- Directly touching WTCH markets: ~21,756
  - Outbound: 21,258
  - Inbound: 498

---

## Full Truckload (FTL) Logic

### What Constitutes a Shipment
- A shipment = 1 `orderCode` (NOT each mainShipment='YES' row)
- FTL orders often have multiple mainShipment='YES' rows (multi-stop routes)
- ~22% of FTL orders are multi-stop (one truck, multiple pickups/deliveries)
- Only count orders where at least one mainShipment='YES' row touches a WTCH crossdock

### Revenue Calculation
**Formula**:
```
Revenue = SUM(revenueAllocationNumber) for ALL warpIds with the same orderCode
```

### Cost Calculation
**Formula**:
```
Cost = SUM(costAllocationNumber) for ALL warpIds with the same orderCode
```

### Multi-Market FTL Orders
6 FTL orders touch multiple WTCH markets (e.g., one truck delivering to both WTCH-SEA and WTCH-PDX).

**Handling**: Split the order by market, allocating revenue/cost/pallets based on the mainShipment='YES' leg that touches each market.

Example (P-63829-2541):
| Market | Legs | Pallets | Revenue | Cost |
|--------|------|--------|---------|------|
| DFW | 1 | 6 | $2,032.63 | $1,693.86 |
| IAH | 1 | 8 | $2,821.15 | $2,350.96 |
| SAT | 1 | 3 | $1,146.23 | $955.19 |
| **Total** | 3 | 17 | $6,000.01 | $5,000.01 |

---

## Pallets Logic

### LTL Pallets
**Formula**:
```
IF mainShipment='YES' pallets > 0:
    Pallets = mainShipment='YES' pallets
ELSE:
    Pallets = SUM(pallets WHERE revenueAllocationNumber > 0 AND NOT crossdock-to-crossdock)
```

**Notes**:
- 99.8% of LTL market shipments have pallets in the mainShipment='YES' row
- Only 41 out of 21,756 have pallets = 0 in mainShipment='YES'
- The fallback SUM approach avoids double-counting (which occurs in 5.6% of normal orders if SUM is always used)

### FTL Pallets
**Formula**:
```
Pallets = SUM(pallets) from all mainShipment='YES' rows in the orderCode
```

**Notes**:
- Multi-stop routes have different pallets per stop (e.g., 26 pallets total across 4 stops: 6, 8, 7, 5)
- Only sum mainShipment='YES' rows - mainShipment='NO' rows (handling fees) may duplicate pallets
- For multi-market orders, allocate pallets based on which mainShipment='YES' leg touches each market

---

## Key Fields Reference
- `warpId`: Unique identifier for each shipment leg
- `orderCode`: Groups multiple warpIds belonging to the same customer order
- `mainShipment`: 'YES' = customer-facing shipment, 'NO' = operational leg
- `revenueAllocationNumber`: Revenue for that row
- `costAllocationNumber`: Cost for that row
- `pieces`: Number of pallets for that row (column name is `pieces` in database)
- `pickLocationName`: Pickup location
- `dropLocationName`: Dropoff location
- `shipmentType`: 'Less Than Truckload', 'Full Truckload', etc.
- `shipmentStatus`: 'Complete', 'Canceled', 'Pending', etc.
- `pickWindowFrom`: Date/time in format MM/DD/YYYY HH:MM:SS

