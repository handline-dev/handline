// The looks the Accounts and Stats pages share: the scrolling page, its header line and title, and the boxed card.
export const PAGE = 'mx-auto h-full w-full overflow-auto px-4 pt-0 pb-6';
export const PAGE_HEAD = 'flex flex-wrap items-center gap-x-3 gap-y-2 pt-4 pb-3';
export const PAGE_TITLE = 'm-0 text-page font-semibold text-ink';
export const CARD = 'rounded-card border border-line bg-surface';
/** A chart card; `chart` stays as the hook the tests query. */
export const CHART = `chart ${CARD} px-4 py-3`;
export const CHART_TITLE = 'm-0 mb-2 text-title font-semibold';
