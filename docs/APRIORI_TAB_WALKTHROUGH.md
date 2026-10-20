# Apriori tab walkthrough

This tab answers two plain questions: **which signals occur together?** and
**do those patterns hold up later?** It is an offline description of past
events. It never scores, reviews, blocks, or changes an event. Labels are used
only to select the comparison subset and in the explicitly marked evaluation
mode.

## What is on the tab

| Element | What it means | Say aloud when demoing |
|---|---|---|
| **Intro and Glossary** | Three lines: what Apriori is, why co-occurrence matters, and the three-step workflow. The collapsible glossary defines event/basket, signal, pattern, support, confidence, lift, prevalence, candidate, and pruning with a one-line example each. | "A basket is one event and the signals found in it. A pattern is a set of signals that keeps appearing together." |
| **Guided demo** | Four numbered steps: run mixed traffic, choose Balanced, read the top pattern, check the time split. Completed steps get a check mark; the status line reports the current step. | "Four steps: load traffic, pick the middle strictness, read the strongest pattern, then test it on later events." |
| **1. Which events?** | **All events** or **Only abuse-labelled events (comparison)**. The second option is a labelled subset used only for comparison; labels never build baskets. | "All events is the normal view. The labelled view is a comparison only." |
| **2. How strict?** | **Broad** = 6% support / 10% confidence, **Balanced** = 12% / 26% (default), **Strict** = 20% / 45%. A higher setting keeps fewer patterns but demands more evidence. Advanced thresholds expose both sliders, and **Use suggested support** follows abuse prevalence. | "Balanced is the default: a pattern must appear in at least 12% of events and be right at least 26% of the time." |
| **3. What was found** | Each pattern is a sentence, not a table row. Itemsets read "A, B, and C appear together in N of M events." Rules read "When A happens, B also happens in X% of cases (seen in N of M events, L times more often than chance)." A strength badge uses lift: **Strong** at 2x or more, **Moderate** from 1.2x to 2x, otherwise **No real link**. **Show all** expands beyond the top five. | "This triple appears in 9 of 42 events; the rule is right every time it fires and is eight times more common than chance." |
| **Matching events** | Selecting a pattern opens the events that contain it, with time, account/device, action, score, and highlighted signal chips. The events are evidence, not new decisions. | "These are the actual events behind the pattern. Apriori did not score them; it only found the common signals." |
| **How Apriori found these** | Collapsible level stepper showing candidates and frequent sets at levels 1, 2 and 3, plus the pruning note. | "Level 1 finds frequent signals, level 2 pairs, level 3 triples. Rare signals are pruned because a larger set containing them must also be rare." |
| **Do the patterns hold up later?** | Mines on the first half by event time and tests the same rules on the second half. Each row shows first-half and second-half confidence and a verdict. **Holds up** means test confidence is above 5%, lift is above 1.1, and confidence did not fall below 75% of the first-half value. **Does not hold up** means it failed one of those checks. **Not enough data** means the premise never appeared in the second half. Label-containing rules are hidden here. | "Finding a pattern in the first half is not enough. This rule was right in the first half but never appeared later, so we cannot trust it yet." |
| **Advanced (evaluation only)** | Optional offline label-as-item mode. It adds `label:abuse` / `label:legitimate` and shows `signal -> label` rules. The warning explains that these are for checking the data, never for scoring, and that rule-derived baskets can make patterns circular. | "This mode uses the answer key. It is not part of the detector." |
| **Advantages / Limitations** | Three short bullets each: readable patterns, tractable pruning and hypothesis generation; co-occurrence is not causation, support can hide rare abuse, and baskets are rule-derived. | "Apriori is good for explanation and hypotheses, not for ranking risk." |
| **Empty, loading, error states** | No pattern at the current strictness: "No patterns at this strictness. Try Broad, or lower the support." No events: prompt to run the guided demo. Loading and failure use plain text with a retry action. | "No patterns here because the threshold is too high; Broad or lower support will show more." |

## Guided demo, step by step

1. Reset the session and open **Apriori**. Balanced is applied automatically on
   the first visit through the existing `/api/apriori` endpoint.
2. Click **Run guided demo**. Step 1 runs the `mixed` scenario; step 2 applies
   Balanced (12% support, 26% confidence). The status line reports each step.
3. Step 3 selects the top pattern. With the 42-event mixed scenario the top
   triple is **Same device, many accounts + Repeated free trial + Throwaway
   email**: 9 of 42 events, 21% support, 100% confidence, 8.0x lift, **Strong**.
   Its matching-events table opens below the list.
4. Step 4 scrolls to **Do the patterns hold up later?** The meta line reads
   "We mine the first 21 events, then check the same rules on the last 21."
   Read the badge on each row: **Holds up**, **Does not hold up**, or **Not
   enough data**. In the default run the label-free `Country mismatch ->
   Failed payment` rule is **Does not hold up** (100% in the first half, 0% in
   the second), while rules whose premise never reappears show **Not enough
   data**.

## Useful checks

- **Too strict:** choose All events, open **Advanced thresholds**, set support
  to 60%. The pattern list shows the empty message, because no signal set
  covers 60% of this sample.
- **Comparison subset:** choose **Only abuse-labelled events**. The counts
  change to 27 analyzed events, but the baskets are still built from signals
  only.
- **Label mode:** open **Advanced (evaluation only)** and enable the checkbox.
  Four label-containing rules appear in both the pattern list and the time
  split. Turning the checkbox off hides them again, and the note under the time
  split reports how many were hidden.
- Labels never enter scoring, Bloom, DGIM, or the normal Apriori baskets.
