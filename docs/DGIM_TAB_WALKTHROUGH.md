# DGIM tab walkthrough

This tab answers two questions for one device or card in five seconds: **is it
abnormal?** and **what did DGIM do to the latest event?** It never changes the
DGIM algorithm, the scoring rule, or the API. Labels are not read by DGIM.

## What is on the tab

| Element | What it means | Say aloud when demoing |
|---|---|---|
| **What is this tab?** | Three plain-language lines: every device/card has a suspicious-bit stream; DGIM estimates the 1s in the latest N with a few buckets; a high estimate plus a suspicious newest event adds +10. | "Each device and card has its own stream. DGIM counts suspicious events without storing the whole window." |
| **Verdict banner** | Always visible. Coloured **BURST**, **WATCH** or **NORMAL**, with an icon and a full sentence naming the estimate, entity type, window N and threshold. | "This device is BURST: six suspicious events in its last sixty, threshold five." |
| **Result line** | States the scoring result for the latest event: `DGIM added +10 ... (event id, score a -> b)` or `DGIM added nothing ... because <reason>`. Reasons are explicit: below threshold, newest event not suspicious, cancel, or (for a card) the device stream was below threshold. | "DGIM added ten here, taking the event from score 90 to 100." |
| **Entities to look at** | Ranked list of devices and cards, highest current DGIM estimate first. Each row shows name, type, estimate, event count, bucket count and a text+icon status badge. The default is the top row; if every entity is NORMAL, the busiest entity is selected and the tab says "Nothing abnormal so far. Run Card-testing or Multi-account to see a burst." | "We start from who is abnormal, not from an empty dropdown." |
| **Filter chips / search** | All / Burst / Watch / Normal filter the ranking. The search box matches exact device or card IDs. | "Filter to Burst to see only the entities worth investigating." |
| **Last N events** | One cell per event, oldest to newest. Red = suspicious (1), grey = clean (0), hollow dashed = cancel (not scored). Hover shows event ID, time, type, action, score and the signal (failed payment, throwaway email, country mismatch, forwarding email). Click opens that event in the Live inspector. | "Red cells are the ones DGIM is counting; hollow cells are cancels and are never scored." |
| **DGIM buckets** | Boxes drawn over the cells they summarise; box width is the bucket size. Caption: "DGIM does not keep these cells. It keeps only these B boxes and estimates the count from them." | "DGIM keeps these four boxes, not the eight cells." |
| **Estimate / Exact / Difference** | Current DGIM estimate, the demo-only exact count over the same cells, and their signed difference. The note explains that a small difference is expected. | "Six estimated, eight exact, two apart. That is the memory-for-accuracy trade." |
| **Chart** | The selected entity over its own event positions: estimate (solid), exact (dashed), threshold (horizontal). Red bands mark estimate >= threshold. A pin labelled "burst detected here" marks the first event where DGIM added +10. | "The estimate crosses the threshold here, and DGIM starts adding ten." |
| **Adjust (collapsed)** | "How many recent events to count (N)" and "How many suspicious events count as a burst". Changing either re-evaluates the verdict immediately and adds a note saying what changed. Stored event scores are not rewritten. | "N counts this entity's events, not seconds." |
| **How DGIM saves memory (collapsed)** | The separate 1000-element scale demo and worst-case stream. The first line is the result: at most 9 buckets instead of 100 stored bits, average error 12.9%, worst 26.9%, bound about 50%. It uses its own stream and window, separate from the entity above. | "This independent demo shows why O(log N) buckets beat storing 100 bits." |
| **Empty / edge states** | No events: "No events for this entity yet." Events but no 1s: grey cells and "No buckets are needed because there are no 1s." Cancels: hollow cells with "Cancel events are not scored." Loading and error states use text and an icon, never colour alone. | "Nothing abnormal here, so DGIM needs no buckets." |

## Reproduce a BURST verdict

1. Reset the session (↺ Reset) and open **Live**.
2. Choose scenario **Card-testing attack** and click **Run scenario**. Wait for the replay to finish.
3. Open the **DGIM** tab. The ranking shows both the device and the card as **BURST**:
   `r1-bot` (device) and `r1-tested-card` (card), estimate 6, threshold 5. If you
   have run the scenario before, the prefix will be `rN-` from your run.
4. Select `r1-bot` (it is the default top-ranked entity). The banner reads:
   "Abnormal: 6 suspicious events among this device's last 60 events (threshold 5)."
   The result line reads: "DGIM added +10 to the latest event (evt_00008, score 90 -> 100)."
5. The stream shows eight red cells, four bucket boxes, estimate 6, exact 8,
   difference -2. The chart pin labelled "burst detected here" is on **evt_00006**,
   the first event where DGIM added +10.
6. To show the +10 in the Live inspector, click the newest cell (`evt_00008`).
   The Live inspector lists `+10 Unusually high recent suspicious activity on
   device (DGIM est. 6/60)` and the exact-rules-to-full line `80 block -> 100`.
   Clicking the pinned cell (`evt_00006`) shows the first +10 instead
   (`DGIM est. 5/60`).

If every entity is NORMAL, the tab selects the busiest entity, shows the NORMAL
banner, and prints "Nothing abnormal so far. Run Card-testing or Multi-account
to see a burst." Run one of those scenarios to produce the steps above.
