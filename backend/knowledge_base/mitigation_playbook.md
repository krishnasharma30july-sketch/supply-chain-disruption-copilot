# Mitigation Playbook

This playbook defines how the operations team responds to freight disruptions. The rules are implemented as code (policy-as-code) and every automated decision cites the section that justifies it.

## Monitoring

If an order sits inside the disruption's geography but its expected delivery moves by less than two days and the risk of missing the promised date stays below 20%, take no customer-facing action. Keep the order on the watch list and re-evaluate if the disruption is extended.

## Proactive notification

If an order still arrives on or before the promised date but its expected delivery moves by two days or more, or the risk of missing the promise reaches 20%, send a short heads-up so the customer is not surprised by tracking changes. Smaller shifts are monitored silently: over-notifying customers whose delivery is on time erodes trust. No compensation is due because the promise is kept.

## Perishable goods

Food, beverages and flowers lose value quickly. If a perishable order will arrive two or more days after the promised date, cancel it proactively and refund it in full (items and freight). Do not ask the customer to wait for goods that may arrive spoiled.

## Essential items

Baby products, hygiene, health and pet supplies are often needed urgently. If an essential order will be three or more days late, keep the shipment moving but offer the customer a no-fee cancellation in addition to the standard compensation.

## Reroute via alternate seller

If the order has not yet been handed to the carrier and the same product is sold by another seller outside the disrupted area, fulfil it from that seller when all of the following hold:
- the alternate seller sold the product within the last 90 days;
- the alternate seller's price is at most 10% higher than the original price (the company absorbs the difference);
- the reroute improves the expected delivery by at least two days;
- the disruption does not affect the destination's last-mile delivery.

## Reroute via alternate route

Small parcels (up to 5 kg billable and 60 cm on the longest side) that are blocked at origin or on the linehaul can move to an alternate carrier or air corridor. This costs about 60% more freight and adds one day to the normal lane time. It is not available when the disruption is nationwide, blocks all transport modes, or affects the last mile. Only reroute if it saves at least one day.

## Bulky and heavy freight

Rerouting bulky freight (over 30 kg billable, any side over 100 cm, or a combined size over 200 cm) is uneconomical and risky. Keep the shipment on its original route, tell the customer about the delay and compensate according to the compensation policy. If the delay exceeds ten days, also offer a no-fee cancellation.

## Gifts and seasonal items

Gifts, toys, party and seasonal goods are bought for a date. If such an order will be late at all, compensate the customer and offer a no-fee cancellation so they can buy an alternative in time.

## Long delays

Any order more than fourteen days late gets a no-fee cancellation offer in addition to compensation, whatever its product type.

## Standard delays

For all other late orders, keep the shipment on its route, send a clear and empathetic update with the new delivery window, and apply the compensation tier that matches the number of days late.

## Mixed orders and split shipments

If an order contains items that are not affected by the disruption, ship the unaffected items immediately (split shipment) and handle the delayed items under the rules above. Orders whose items fall under different actions are flagged for review; the reviewer may only choose among the actions the rules allow for that order.
