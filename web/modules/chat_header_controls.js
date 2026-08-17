import { apiFetch } from './api_client.js';
import { headerBudgetPresentation } from './costs.js';

// The global agent controls in ONE chat instance's overlay header: the
// Evolve/Consciousness toggles and their "something is active" dot on the More
// summary, the context-mode segment, and the budget pill — all projected from a
// single /api/state read, with an unavailable backend rendering as an explicit
// unavailable accounting rather than a stale number. The instance's header node,
// its id-scoped lookup and the shared page state are handed over explicitly.
export function createHeaderControls({ byId, headerActions, state }) {
    function syncHeaderControlState(data) {
        headerActions?.querySelectorAll('[data-chat-command]').forEach((button) => {
            const cmd = button.dataset.chatCommand;
            if (cmd === 'evolve') {
                button.classList.toggle('on', !!data?.evolution_enabled);
                if (data?.evolution_state?.detail) button.title = data.evolution_state.detail;
            } else if (cmd === 'bg') {
                button.classList.toggle('on', !!data?.bg_consciousness_enabled);
                if (data?.bg_consciousness_state?.detail) button.title = data.bg_consciousness_state.detail;
            }
        });
        // Evolve/Consciousness now live inside the More menu; surface a small dot
        // on the More summary so an active mode stays visible without opening it.
        const moreSummary = headerActions?.querySelector('.chat-header-more > summary');
        if (moreSummary) {
            const anyActive = !!data?.evolution_enabled || !!data?.bg_consciousness_enabled;
            moreSummary.classList.toggle('has-active', anyActive);
        }
        const ctxBtn = byId('context-mode');
        if (ctxBtn && typeof data?.context_mode === 'string') {
            ctxBtn.dataset.contextMode = data.context_mode === 'low' ? 'low' : 'max';
        }
        const budget = headerBudgetPresentation(data);
        const budgetText = byId('budget-text');
        const budgetFill = byId('budget-bar-fill');
        if (budgetText) budgetText.textContent = budget.label;
        if (budgetFill) budgetFill.style.width = `${budget.fillPct}%`;
    }

    async function refreshHeaderControlState(force = false) {
        if (!force && state.activePage !== 'chat') return;
        try {
            const resp = await apiFetch('/api/state', { cache: 'no-store' });
            if (!resp.ok) {
                syncHeaderControlState({ accounting: { available: false } });
                return;
            }
            syncHeaderControlState(await resp.json());
        } catch {
            syncHeaderControlState({ accounting: { available: false } });
        }
    }

    return { syncHeaderControlState, refreshHeaderControlState };
}
