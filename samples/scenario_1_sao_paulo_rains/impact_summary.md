# Impact report: Heavy rains delaying freight leaving Sao Paulo

- **Run:** `4aa90221-bd7e-4c62-91a4-a4ae79a7d440` | status **succeeded** | LLM mode: online
- **Scenario:** Heavy rains are delaying all freight leaving Sao Paulo for the next 5 days
- **Interpretation (llm, confidence 0.95):** weather | origin: states SP | destination: - | legs: first_mile, linehaul | 5.0 days from 2018-08-01T00:00 + 2.0 recovery days
- **Ambiguity:** 'Sao Paulo' interpreted as Sao Paulo state (SP) ('Sao Paulo' is both a state and a city. Defaulting to the state for freight disruptions; switch to the city if the event is local.)

## Heavy rains delaying freight leaving Sao Paulo impacting 681 orders and R$ 106,167.17 in GMV.

Heavy rains delaying freight leaving Sao Paulo have created an ongoing supply chain disruption spanning a duration of 5.0 days with a forecasted recovery of 2.0 days. Out of 1,409 orders in scope, exactly 681 orders are delayed, impacting 679 customers, 329 sellers, and 624 SKUs. This delay puts 398 orders breaching promise, representing 58 percent of delayed breaching, which includes 288 orders newly breaching. The disruption has placed R$ 106,167.17 in GMV delayed brl at risk with an average hold of 6.4 days, affecting 6 bulky orders delayed and a severity breakdown of 8 critical, 164 high, 267 medium, and 242 low severity counts. Top impacted categories include Health Beauty with 64 delayed orders, Housewares with 64 delayed orders, and Bed Bath Table with 63 delayed orders, while the top origin states by delayed items is SP with 811 delayed items. To address this, the mitigation plan deploys specific operational actions including cancel and refund (perishable) for 10, delay with compensation for 62, monitor only (no customer contact) for 728, proactive heads-up (still on time) for 283, reroute via alternate carrier/air for 325, and re-source from an alternate seller for 1, keeping total estimated mitigation cost brl at R$ 9,654.09.

## Key figures

| Metric | Value |
|---|---|
| Orders in scope | 1409 |
| Orders delayed | 681 |
| Orders missing the promised date | 398 |
| ...of which only because of this disruption | 288 |
| Customers affected | 679 |
| Sellers affected | 329 |
| SKUs affected | 624 |
| Order value delayed | R$ 106,167.17 |
| Billable weight delayed | 2,627.5 kg |
| Average hold | 6.4 days |
| Bulky orders delayed | 6 |
| Severity (critical/high/medium/low) | 8/164/267/242 |
| Estimated mitigation cost | R$ 9,654.09 |

### Decisions

| Decision | Rule code | Orders |
|---|---|---|
| monitor only (no customer contact) | `MONITOR` | 728 |
| proactive heads-up (still on time) | `NOTIFY_ONLY` | 283 |
| cancel and refund (perishable) | `CANCEL_REFUND` | 10 |
| reroute via alternate carrier/air | `REROUTE_ALT_ROUTE` | 325 |
| re-source from an alternate seller | `REROUTE_ALT_SELLER` | 1 |
| delay with compensation | `DELAY_WITH_COMPENSATION` | 62 |

### Recommendations

- Assign customer care to call the 8 critical-severity customers before the emails go out.
- Pre-book alternate carrier or air capacity for 325 parcel orders blocked on the affected legs.
- Confirm stock with the alternate sellers for 1 order being re-sourced outside the disrupted area.
- Contact the most exposed sellers to hold or re-plan handovers: 6560211a (Sao Paulo/SP, 25 orders), 7c67e144 (Itaquaquecetuba/SP, 24 orders), 1025f0e2 (Sao Paulo/SP, 19 orders).
- Review and approve the customer emails, starting with the 398 orders that will miss the promised date (sorted by severity on the Customer emails tab).
- Plan dedicated recovery capacity for 6 bulky orders; bulky freight clears the backlog last.
- Process 10 proactive cancellations of perishable goods and trigger the refunds.

### Most affected categories

| Category | Orders | Items | Value (R$) |
|---|---|---|---|
| Health Beauty | 64 | 65 | 8,331.83 |
| Housewares | 64 | 89 | 13,313.82 |
| Bed Bath Table | 63 | 82 | 10,603.72 |
| Watches Gifts | 44 | 46 | 9,931.88 |
| Sports Leisure | 42 | 50 | 4,738.47 |
| Auto | 39 | 40 | 4,217.59 |
| Furniture Decor | 38 | 64 | 9,139.92 |
| Baby | 34 | 35 | 4,220.72 |
| Office Furniture | 30 | 41 | 7,914.35 |
| Telephony | 26 | 27 | 1,324.57 |

### Most exposed sellers

| Seller | Location | Orders | Value (R$) |
|---|---|---|---|
| 6560211a | Sao Paulo/SP | 25 | 1,840.67 |
| 7c67e144 | Itaquaquecetuba/SP | 24 | 6,150.51 |
| 1025f0e2 | Sao Paulo/SP | 19 | 5,282.49 |
| d91fb3b7 | Praia Grande/SP | 19 | 1,679.65 |
| beadbee3 | Guarulhos/SP | 14 | 1,384.19 |
| 16090f2c | Atibaia/SP | 9 | 400.22 |
| 213b25e6 | Salto/SP | 9 | 1,257.95 |
| c70c1b0d | Hortolandia/SP | 9 | 1,066.23 |
| e9779976 | Praia Grande/SP | 9 | 689.77 |
| da8622b1 | Piracicaba/SP | 8 | 820.91 |

### Retrieval evidence (hybrid search)

- **Most relevant impacted orders**: Order 213387D0 | awaiting carrier pickup | Mogi das Cruzes/SP -> Sao Paulo/SP (0.0232), Order D78BEF62 | in transit | Sao Paulo/SP -> Jales/SP (0.0241), Order 929DDF60 | in transit | Sao Paulo/SP -> Viamao/RS (0.0311)
- **Heavy / bulky impacted products**: Housewares | 47.1 kg | BULKY (0.0315), Office Furniture | 13.9 kg | BULKY (0.0269), Furniture Decor | 17.0 kg | BULKY (0.0313)
- **Most exposed impacted sellers**: Seller 4deebb31 | Sao Paulo, SP (0.0239), Seller 323ce52b | Sao Paulo, SP (0.0229), Seller 189ca353 | Sao Paulo, SP (0.0259)
- **Applicable policy**: Mitigation Playbook: Bulky and heavy freight (0.0294), Mitigation Playbook: Standard delays (0.0295), Mitigation Playbook: Long delays (0.0291)

## Top 25 orders by severity

| Order | Stage | Lane | Category | kg | Days late | Severity | Action |
|---|---|---|---|---|---|---|---|
| BC9C2FB1 | awaiting_pickup | SP->MG | baby | 26.0 | 22.8 | 81 (critical) | delay with compensation |
| A98F7931 | awaiting_pickup | SP->SP | bed bath table | 21.0 | 15.8 | 81 (critical) | delay with compensation |
| 70A3ACB4 | awaiting_pickup | SP->BA | bed bath table | 21.0 | 9.8 | 80 (critical) | delay with compensation |
| 87789201 | awaiting_pickup | SP->PR | health beauty | 14.6 | 7.0 | 78 (critical) | delay with compensation |
| 9254EA56 | awaiting_pickup | SP->SP | office furniture | 22.7 | 8.8 | 77 (critical) | delay with compensation |
| 97A0C369 | in_transit | SP->SP | housewares | 18.1 | 9.4 | 75 (critical) | delay with compensation |
| 64958ED6 | in_transit | SP->SP | bed bath table | 16.9 | 5.3 | 75 (critical) | delay with compensation |
| 9F1887EE | awaiting_pickup | SP->BA | sports leisure | 13.3 | 6.8 | 75 (critical) | delay with compensation |
| E326F990 | in_transit | SP->SP | signaling and security | 14.7 | 5.5 | 74 (high) | delay with compensation |
| 17CA01C2 | awaiting_pickup | SP->SP | furniture decor | 31.5 | 3.8 | 73 (high) | delay with compensation |
| 040FFB80 | awaiting_pickup | SP->SP | kitchen dining laundry garden furniture | 16.8 | 5.8 | 72 (high) | delay with compensation |
| 9B0F1E14 | in_transit | SP->SP | pet shop | 12.3 | 9.5 | 72 (high) | delay with compensation |
| D0917C0A | in_transit | SP->PR | housewares | 5.4 | 6.5 | 70 (high) | delay with compensation |
| 6B69FF28 | in_transit | SP->SP | housewares | 8.6 | 9.7 | 70 (high) | delay with compensation |
| 25348D7A | in_transit | SP->SP | watches gifts | 0.7 | 16.4 | 69 (high) | reroute via alternate carrier/air |
| 15C27084 | in_transit | SP->RJ | furniture decor | 6.1 | 8.4 | 68 (high) | delay with compensation |
| A5D865E1 | awaiting_pickup | SP->SP | drinks, health beauty | 1.9 | 8.8 | 68 (high) | cancel and refund (perishable) |
| 9E707669 | in_transit | SP->SP | costruction tools garden | 10.0 | 8.5 | 67 (high) | delay with compensation |
| 6C220817 | awaiting_pickup | SP->SP | garden tools | 3.2 | 8.8 | 67 (high) | reroute via alternate carrier/air |
| E1FAF921 | awaiting_pickup | SP->RJ | bed bath table | 5.0 | 10.8 | 67 (high) | reroute via alternate carrier/air |
| 0E1DDBE4 | in_transit | SP->SP | housewares | 10.2 | 3.5 | 67 (high) | delay with compensation |
| A49B8F92 | in_transit | SP->SP | pet shop | 12.0 | 4.4 | 67 (high) | delay with compensation |
| FD607D0A | awaiting_pickup | SP->BA | furniture decor | 2.5 | 6.8 | 66 (high) | reroute via alternate carrier/air |
| CDCC163C | pending_approval | SP->SP | musical instruments | 9.4 | 4.8 | 66 (high) | delay with compensation |
| 66A4FA2D | in_transit | SP->SP | costruction tools tools | 4.7 | 9.4 | 66 (high) | reroute via alternate carrier/air |

## Sample customer emails

### To Debora (orders BC9C2FB1): llm, QA passed

**Subject:** Update on your order BC9C2FB1

```text
Hello Debora, we sincerely apologise for the delay with your order. Your order was already running behind its promised date, and severe weather in Sao Paulo added to the delay.

For order BC9C2FB1 containing 1 x Baby, the promised date was Jul 24, 2018. Your order is still on its way and is now expected between Aug 15, 2018 and Aug 23, 2018.

As compensation, you will receive a freight refund of R$ 140.91, which gives a refund total of R$ 140.91, and a 15% voucher for R$ 58.50 with code SORRY-39E5FF, valid for 90 days.

You can track your order at https://shop.example.com/account/orders, cancel it at https://shop.example.com/account/orders/cancel, or contact our team at https://shop.example.com/help. A member of the care team will also contact you personally.

Thank you for your patience.

The Customer Care Team
```

### To Bianca (orders A98F7931): llm, QA passed

**Subject:** Update on your order A98F7931

```text
Hello Bianca, we sincerely apologise for the delay with your order. Your order was already running behind its promised date, and severe weather in Sao Paulo added to the delay.

For order A98F7931 containing 1 x Bed Bath Table, the promised date was Jul 27, 2018. Your order is still on its way and is now expected between Aug 11, 2018 and Aug 17, 2018.

As compensation, you will receive a freight refund of R$ 48.68, which gives a refund total of R$ 48.68, and a 15% voucher for R$ 100.00 with code SORRY-CFC9A2, valid for 90 days.

You can track your order at https://shop.example.com/account/orders, cancel it at https://shop.example.com/account/orders/cancel, or contact our team at https://shop.example.com/help. A member of the care team will also contact you personally.

Thank you for your patience.

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
