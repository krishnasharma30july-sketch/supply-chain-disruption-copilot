# Impact report: Last-mile courier strike in Rio de Janeiro

- **Run:** `e63859e0-844e-4374-a539-d9bbb4845248` | status **succeeded** | LLM mode: online
- **Scenario:** Last-mile courier strike in Rio de Janeiro for 3 days
- **Interpretation (llm, confidence 1.0):** strike | origin: - | destination: states RJ | legs: last_mile | 3.0 days from 2018-08-01T00:00 + 1.0 recovery days
- **Ambiguity:** 'Rio de Janeiro' interpreted as Rio de Janeiro state (RJ) ('Rio de Janeiro' is both a state and a city. Defaulting to the state for freight disruptions; switch to the city if the event is local.)

## Last-mile courier strike in Rio de Janeiro impacts 81 orders over 3.0 days.

A recent disruption caused by a last-mile courier strike in Rio de Janeiro has placed 270 orders in scope, leading to 81 orders delayed and 81 customers delayed across 65 sellers and 81 SKUs delayed. The total GMV delayed is R$ 16,524.62, with an average hold days of 3.2. Among the delayed volume, 23 orders are breaching promise, representing 28 percent of delayed breaching, while 9 orders are newly breaching. Severity is distributed across 2 critical, 9 high, 18 medium, and 52 low incidents, with Health Beauty leading categories at 13 delayed orders, followed by Watches Gifts with 12 and Sports Leisure with 9. Origin states contributing to delayed items include SP with 42, RJ with 24, and MG with 4. To address this situation with an estimated mitigation cost of R$ 924.05, operations will apply specific decisions comprising 23 for delay with compensation, 199 for monitor only (no customer contact), and 48 for proactive heads-up (still on time) to secure the recovery days of 1.0.

## Key figures

| Metric | Value |
|---|---|
| Orders in scope | 270 |
| Orders delayed | 81 |
| Orders missing the promised date | 23 |
| ...of which only because of this disruption | 9 |
| Customers affected | 81 |
| Sellers affected | 65 |
| SKUs affected | 81 |
| Order value delayed | R$ 16,524.62 |
| Billable weight delayed | 222.2 kg |
| Average hold | 3.2 days |
| Bulky orders delayed | 0 |
| Severity (critical/high/medium/low) | 2/9/18/52 |
| Estimated mitigation cost | R$ 924.05 |

### Decisions

| Decision | Rule code | Orders |
|---|---|---|
| monitor only (no customer contact) | `MONITOR` | 199 |
| proactive heads-up (still on time) | `NOTIFY_ONLY` | 48 |
| delay with compensation | `DELAY_WITH_COMPENSATION` | 23 |

### Recommendations

- Assign customer care to call the 2 critical-severity customers before the emails go out.
- Coordinate with last-mile partners in the affected destinations (RJ (81) delayed orders) to queue deliveries for the first days after the disruption, oldest promises first.
- Review and approve the customer emails, starting with the 23 orders that will miss the promised date (sorted by severity on the Customer emails tab).

### Most affected categories

| Category | Orders | Items | Value (R$) |
|---|---|---|---|
| Health Beauty | 13 | 14 | 2,870.85 |
| Watches Gifts | 12 | 12 | 4,820.80 |
| Sports Leisure | 9 | 11 | 1,710.86 |
| Bed Bath Table | 8 | 8 | 688.38 |
| Housewares | 8 | 8 | 1,454.08 |
| Baby | 4 | 4 | 400.32 |
| Furniture Living Room | 4 | 4 | 386.89 |
| Furniture Decor | 3 | 3 | 296.74 |
| Telephony | 3 | 3 | 1,411.06 |
| Auto | 2 | 2 | 161.88 |

### Most exposed sellers

| Seller | Location | Orders | Value (R$) |
|---|---|---|---|
| 620c87c1 | Petropolis/RJ | 5 | 308.65 |
| 8b9d6eec | Mesquita/RJ | 4 | 386.89 |
| 2eb70248 | Campinas/SP | 3 | 515.41 |
| 537eb890 | Rio De Janeiro/RJ | 3 | 86.18 |
| 06a2c3af | Sao Luis/MA | 2 | 235.76 |
| 1025f0e2 | Sao Paulo/SP | 2 | 265.48 |
| 1835b56c | Ibitinga/SP | 2 | 136.05 |
| 4869f7a5 | Guariba/SP | 2 | 352.19 |
| 4a3ca931 | Ibitinga/SP | 2 | 211.55 |
| 955fee92 | Sao Paulo/SP | 2 | 941.71 |

### Retrieval evidence (hybrid search)

- **Most relevant impacted orders**: Order CB8AC0A6 | awaiting carrier pickup | Rio de Janeiro/RJ -> Rio de Janeiro/RJ (0.0250), Order 7E272771 | awaiting carrier pickup | Sao Paulo/SP -> Rio de Janeiro/RJ (0.0204), Order 20FEBE29 | awaiting carrier pickup | Sao Paulo/SP -> Rio de Janeiro/RJ (0.0229)
- **Heavy / bulky impacted products**: Furniture Decor | 6.0 kg | BULKY (0.0325), Furniture Living Room | 49.0 kg | BULKY (0.0308), Home Appliances | 1.5 kg | PARCEL (0.0262)
- **Most exposed impacted sellers**: Seller 2aa3443d | Rio de Janeiro, RJ (0.0318), Seller 4342d4b2 | Rio de Janeiro, RJ (0.0291), Seller 634964b1 | Rio de Janeiro, RJ (0.0277)
- **Applicable policy**: Mitigation Playbook: Bulky and heavy freight (0.0290), Mitigation Playbook: Standard delays (0.0305), Mitigation Playbook: Long delays (0.0306)

## Top 25 orders by severity

| Order | Stage | Lane | Category | kg | Days late | Severity | Action |
|---|---|---|---|---|---|---|---|
| 29635493 | in_transit | SP->RJ | office furniture | 29.7 | 12.5 | 80 (critical) | delay with compensation |
| 47C195B6 | in_transit | MG->RJ | housewares | 11.4 | 6.5 | 77 (critical) | delay with compensation |
| FD3FBD86 | in_transit | SP->RJ | sports leisure | 3.6 | 10.5 | 67 (high) | delay with compensation |
| A6A81EF0 | in_transit | RJ->RJ | furniture living room | 7.6 | 12.5 | 65 (high) | delay with compensation |
| B8197DDD | in_transit | SP->RJ | watches gifts | 0.3 | 5.5 | 61 (high) | delay with compensation |
| A70E674C | in_transit | SP->RJ | bed bath table | 3.3 | 19.5 | 61 (high) | delay with compensation |
| ADDDC7AD | in_transit | SP->RJ | sports leisure | 1.0 | 12.5 | 61 (high) | delay with compensation |
| 03138C29 | in_transit | SP->RJ | bed bath table | 0.8 | 23.5 | 57 (high) | delay with compensation |
| 87BAD102 | in_transit | SP->RJ | baby | 4.2 | 9.5 | 56 (high) | delay with compensation |
| 413ACAAA | in_transit | SP->RJ | electronics | 0.7 | 11.5 | 55 (high) | delay with compensation |
| C05E92BB | in_transit | SP->RJ | housewares | 0.8 | 9.5 | 54 (high) | delay with compensation |
| 88CA9D74 | in_transit | SP->RJ | pet shop | 7.3 | 3.5 | 49 (medium) | delay with compensation |
| 7DA4F8EC | awaiting_pickup | RJ->RJ | health beauty | 2.9 | 3.0 | 47 (medium) | delay with compensation |
| 3B4AD687 | in_transit | SP->RJ | watches gifts | 0.8 | 2.5 | 44 (medium) | delay with compensation |
| 231E08DD | in_transit | SP->RJ | furniture decor | 5.2 | 2.5 | 44 (medium) | delay with compensation |
| B3D71A20 | in_transit | BA->RJ | telephony | 0.5 | 2.5 | 43 (medium) | delay with compensation |
| EE43FA4A | in_transit | MG->RJ | baby | 1.6 | 4.5 | 42 (medium) | delay with compensation |
| 70DD6272 | in_transit | RJ->RJ | auto | 0.9 | 4.0 | 40 (medium) | delay with compensation |
| 1B5C8FC4 | in_transit | SP->RJ | bed bath table | 3.5 | 2.5 | 38 (medium) | delay with compensation |
| DAB8A6C6 | in_transit | MG->RJ | cool stuff | 2.5 | 2.5 | 38 (medium) | delay with compensation |
| 20512B46 | in_transit | RJ->RJ | bed bath table | 0.3 | 3.5 | 36 (medium) | delay with compensation |
| 78E56715 | in_transit | RJ->RJ | health beauty | 2.0 | 2.5 | 34 (medium) | delay with compensation |
| 81935E52 | in_transit | SP->RJ | baby | 15.1 | 0.0 | 34 (medium) | proactive heads-up (still on time) |
| 7A810792 | in_transit | RJ->RJ | stationery | 0.2 | 3.0 | 33 (medium) | delay with compensation |
| C225A1D1 | in_transit | SP->RJ | watches gifts | 0.5 | 0.0 | 29 (medium) | proactive heads-up (still on time) |

## Sample customer emails

### To Beatriz (orders 29635493): llm, QA passed

**Subject:** Update on your order 29635493

```text
Hello Beatriz,

We are very sorry for the delay with your order. Your order was already running behind its promised date of Jul 24, 2018, and a transport strike in Rio de Janeiro adds to the delay.

For order 29635493 containing 1 x Office Furniture, your order is still on its way and is now expected on or around Aug 5, 2018.

To make things right, we are issuing a freight refund of R$ 142.65, bringing your total refund to R$ 142.65. We are also giving you a 15% voucher for R$ 90.00, with code SORRY-CE8568, valid for 90 days.

You can track the progress of your delivery using https://shop.example.com/account/orders. If you have any questions, please reach out to our team via https://shop.example.com/help. A member of our care team will also contact you personally to follow up.

Thank you for your patience and understanding.

The Customer Care Team
```

### To Beatriz (orders 47C195B6): llm, QA passed

**Subject:** Update on your order 47C195B6

```text
Hello Beatriz,

We are sincerely sorry that your delivery is late. Your order was already running behind its promised date of Jul 30, 2018, and a transport strike in Rio de Janeiro adds to the delay.

Regarding order 47C195B6 with 1 x Housewares, your order is still on its way and is now expected on or around Aug 5, 2018.

As compensation, we are providing a freight refund of R$ 75.81, making your refund total R$ 75.81. You will also receive a 10% voucher worth R$ 26.50. Use code SORRY-2AE340, which is valid for 90 days.

You can follow your package at https://shop.example.com/account/orders and contact support at https://shop.example.com/help. A member of our customer care team will contact you personally to assist further.

Thank you for bearing with us.

The Customer Care Team
```


## Assumptions

- **A1 Simulation clock:** 'Now' is a configurable AS_OF instant (default 2018-08-01 00:00). Order state is reconstructed from timestamps at or before AS_OF; later timestamps are masked.
- **A2 Display date rebasing:** Emails can optionally shift dates so that AS_OF maps to today (run option date_rebase).
- **A3 Order stages from timestamps:** Stage = the most advanced timestamp at or before AS_OF: delivered > in_transit (carrier handover) > awaiting_pickup (approved) > pending_approval. Orders without items, and orders whose final status is canceled/unavailable, are excluded.
- **A4 SLA definitions:** The customer SLA is order_estimated_delivery_date. The seller SLA is shipping_limit_date.
- **A5 Freight leg model:** Shipments leave from the seller's city/state (first mile), travel an inter-state lane (linehaul) and are delivered in the customer's city/state (last mile, final day). There are no hubs or carriers in the data.
- **A6 Lane transit model:** Transit time = delivered_customer - delivered_carrier, as an empirical distribution per seller_state -> customer_state, built from deliveries before AS_OF. Lanes with fewer than 30 samples fall back to the destination-state distribution, then to the national one.
- **A7 Regional exposure of the linehaul:** A shipment spends its first day after carrier handover inside the origin region and its last day before the last mile inside the destination region. A regional disruption only holds shipments that are inside the region while it is active; nationwide and lane-specific disruptions expose the whole inter-state journey.
- **A8 Backlog recovery and partial impact:** A held shipment waits until the disruption ends (scaled by partial_impact_factor, default 1.0), and the backlog then adds ceil(0.3 x duration) days.
- **A9 Billable weight:** Billable weight = max(actual kg, L x W x H / 6000).
- **A10 Handling profiles:** PARCEL (<=5 kg billable and max dimension <=60 cm), MEDIUM (<=30 kg), BULKY (>30 kg billable, any dimension >100 cm, or L+W+H >200 cm).
- **A11 Product classes:** A curated category->class map (PERISHABLE, ESSENTIAL, FRAGILE, HIGH_VALUE_TECH, GIFT_SEASONAL, FURNITURE_HOME, STANDARD). Products without a category get STANDARD with low confidence.
- **A12 Alternate sellers:** The same product_id sold by another seller outside the disruption scope within the last 90 days is a reroute candidate. Stock is assumed to be available.
- **A13 Alternate route:** PARCEL-profile shipments can switch to an alternate carrier/air corridor (lane p50 + 1 day) at +60% freight cost, unless the disruption is nationwide or blocks all modes.
- **A14 Customer identity:** Olist has no names or emails. Greeting names are synthetic, chosen deterministically from customer_unique_id. Approved emails are sent over SMTP to placeholder addresses on a reserved domain (a local Mailpit inbox in the demo stack).
- **A15 Language:** Emails default to English; pt-BR is supported as a run option.
- **A16 Company policy:** The mitigation and compensation policy (config/policy.yaml and knowledge_base/) was authored for this system.
- **A17 One email per customer:** A customer with several affected orders receives one consolidated email per run.
- **A18 Place-name ambiguity:** Names that are both a state and a city (Sao Paulo, Rio de Janeiro) are resolved by qualifiers ('state of', 'city of'); otherwise they default to the state and are flagged so the user can switch.
- **A19 Corpus:** The 10,000 most recent non-canceled orders purchased at or before AS_OF are loaded and indexed. Only non-delivered ones are actionable.
