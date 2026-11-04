# Design tickets: Inventory supply (inventory_supply.js)

Page JS owner: proj-inventory-supply. Details and screenshots: `DESIGN_REVIEW.md`.

1. **The reorder answer is below the fold (medium).** The TLDR answers "what runs out first and when must it be ordered" well ("Vital Bull at the International 3PL runs out Nov 4, 2026*; the order was due Aug 22, 2026."), but the "Data checks" card (8 items, each 2 lines) sits between the TLDR and the stock-out and reorder tables, so at 1440x900 the first reorder heading is at y=870 and at 768px it is two screens down. Change: collapse "Data checks" to one line of chips ("8 checks to look at" with the warn chip) that expands on click, and put the reorder list directly under the TLDR. Done when: the reorder table's header is visible without scrolling at 1440x900.
2. **Hundreds of date inputs on a phone (low).** At 390px the purchase-order table renders about 240 date inputs and 135 small buttons. They are now 40px tall on touch, but the table is long; consider a card layout per order under 640px (product, units, land date, status chip; tap to edit).
