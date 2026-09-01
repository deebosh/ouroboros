// Bounded per-task presentation ledgers for a chat instance (issue #135).
// Extracted from chat.js at its shrink-only byte-ratchet boundary: the
// always-alive Main instance accumulated liveCardRecords and its per-task
// satellites forever (destroy() — the only cleanup — never runs for Main).
// chat.js wires its instance Maps in once and keeps the original call names
// via destructuring; every keeper mutates those same Maps in place.
import { REUSABLE_TASK_IDS } from './task_control_menu.js';

// Caps mirror the capped neighbors already in chat.js (persistedHistory 200,
// pendingSuggestedNames 100): records of UNFINISHED tasks are never evicted.
export const LIVE_CARD_RECORDS_CAP = 200;
export const SKILL_REVIEW_DETAIL_CAP = 200;
export const CONCLUDED_ACTIVITY_LEDGER_MAX = 200;
export const RETIRED_TASK_IDS_CAP = 2000;

// The job-keyed review-detail cache IS a Map subclass, not a facade: its one
// consumer type-gates on `store instanceof Map` (skill_review_card.js) and
// silently discards anything else, so a wrapper object would disable both the
// cache and the cap. Every insert trims FIFO on the store's own key space.
export class BoundedDetailMap extends Map {
    set(key, value) {
        super.set(key, value);
        while (this.size > SKILL_REVIEW_DETAIL_CAP) {
            this.delete(this.keys().next().value);
        }
        return this;
    }
}

export function createChatLedgers({
    taskKey,
    liveCardRecords,
    taskUiStates,
    retiredTaskIds,
    explicitCardExpansion,
    reviewDisclosureByTask,
    skillReviewDetailStore,
    pendingSuggestedNames,
    cancelableTaskIds,
    concludedDirectActivities,
    activeDirectActivities,
    missingManagedTaskIds,
    subagentChildParents = null,
    reviewHydrator = null,
}) {
    function retireId(id) {
        // Bounded anti-duplicate memory: FIFO past the cap (mirrors the
        // seenMessageKeys 2000 idiom). Ids old enough to be evicted here are
        // far outside the history-replay window, so losing the marker cannot
        // mint a duplicate beside a live card.
        if (REUSABLE_TASK_IDS.has(id) || id === '') return;
        retiredTaskIds.delete(id);
        retiredTaskIds.add(id);
        while (retiredTaskIds.size > RETIRED_TASK_IDS_CAP) {
            retiredTaskIds.delete(retiredTaskIds.keys().next().value);
        }
    }

    function recordConcludedActivity(activityId) {
        const aid = taskKey(activityId);
        if (!aid) return;
        missingManagedTaskIds.delete(aid);
        concludedDirectActivities.delete(aid);
        concludedDirectActivities.set(aid, Date.now());
        while (concludedDirectActivities.size > CONCLUDED_ACTIVITY_LEDGER_MAX) {
            const oldest = concludedDirectActivities.keys().next().value;
            concludedDirectActivities.delete(oldest);
        }
    }

    function recordTerminalActivity(taskId) {
        const id = taskKey(taskId);
        if (!id) return;
        activeDirectActivities.delete(id);
        missingManagedTaskIds.delete(id);
        if (REUSABLE_TASK_IDS.has(id)) concludedDirectActivities.delete(id);
        else recordConcludedActivity(id);
    }

    function scheduleTaskUiCleanup(taskState, delayMs = 120000) {
        if (!taskState) return;
        if (taskState.cleanupTimer) clearTimeout(taskState.cleanupTimer);
        taskState.cleanupTimer = setTimeout(() => {
            taskUiStates.delete(taskState.taskId);
            // Keep the finished card interactive, but mark it retired so routine
            // syncs do not rebuild duplicates. Reload/reconnect clears this set.
            retireId(taskState.taskId);
        }, delayMs);
    }

    function descendantRecordIds(rootId) {
        // BFS over the whole lineage: subagent trees nest (depth 3 by default),
        // so eligibility and cascade must see grandchildren, not only children.
        if (!subagentChildParents) return [];
        const ids = [];
        const visited = new Set([rootId]);
        const queue = [rootId];
        while (queue.length) {
            const current = queue.shift();
            for (const [childId, meta] of subagentChildParents) {
                // The visited set terminates on cyclic lineage metadata (two
                // individually valid frames can form A -> B -> A).
                if (meta?.parentId !== current || visited.has(childId)) continue;
                visited.add(childId);
                if (liveCardRecords.has(childId)) ids.push(childId);
                queue.push(childId);
            }
        }
        return ids;
    }

    function evictionBlocked(recordId) {
        const record = liveCardRecords.get(recordId);
        if (!record?.finished) return true; // unfinished work is never evicted
        // A converted card is the task's project pointer — keep it.
        return record?.root?.dataset?.projectCreated === '1';
    }

    function evictOne(victimId) {
        const record = liveCardRecords.get(victimId);
        const taskState = taskUiStates.get(victimId);
        if (taskState?.cleanupTimer) clearTimeout(taskState.cleanupTimer);
        taskUiStates.delete(victimId);
        record?.root?.remove?.();
        liveCardRecords.delete(victimId);
        retireId(victimId);
        explicitCardExpansion.delete(victimId);
        reviewDisclosureByTask.delete(victimId);
        pendingSuggestedNames.delete(victimId);
        cancelableTaskIds.delete(victimId);
        // Forget hydration state too: a late reference may legitimately
        // recreate the card, and a stale appliedRevision would make that
        // fresh card short-circuit into a permanently blank Reviews block.
        reviewHydrator?.drop?.(victimId);
        return victimId;
    }

    function evictFinishedCardsOverCap() {
        const evicted = [];
        while (liveCardRecords.size > LIVE_CARD_RECORDS_CAP) {
            let victimId = null;
            for (const [id] of liveCardRecords) {
                // The victim and its ENTIRE live lineage must be evictable:
                // an unfinished or converted descendant anywhere below keeps
                // the whole composite (its DOM nests under this root).
                if (evictionBlocked(id)) continue;
                if (descendantRecordIds(id).some(evictionBlocked)) continue;
                victimId = id;
                break;
            }
            if (victimId === null) break;
            // Same shape as chat.js's ephemeral-conversion cleanup: the DOM card
            // goes WITH its record (card handlers close over the record, so a
            // kept card would pin it in memory anyway), and the retired id makes
            // a late frame mint at most one fresh card instead of a duplicate
            // beside a zombie. Matches what a reload already shows the user:
            // history rebuilds only the recent window.
            const lineage = descendantRecordIds(victimId);
            evicted.push(evictOne(victimId));
            // The victim subtree carried every descendant's DOM; evict their
            // records with it so nothing keeps pointing at detached nodes
            // (the victim guard above proved the whole lineage evictable).
            for (const descendantId of lineage) evicted.push(evictOne(descendantId));
        }
        return evicted;
    }

    return {
        recordConcludedActivity,
        recordTerminalActivity,
        scheduleTaskUiCleanup,
        evictFinishedCardsOverCap,
    };
}
