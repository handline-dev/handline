// An app an agent started (HANDLINE_AGENT or OCP_AGENT is set) runs out of the founder's sight: no window shown, no dock icon, no
// tray, no notice, no focus taken. The founder's own app (`pnpm app`, HANDLINE_FOLLOW) is always shown, and HANDLINE_SHOW=1 shows
// any. A hidden window still loads and paints, so a check reads it over the DevTools port (HANDLINE_DEBUG_PORT) or capturePage.
export const runsHidden = (e: NodeJS.ProcessEnv): boolean =>
  Boolean(e.HANDLINE_AGENT || e.OCP_AGENT) && !(e.HANDLINE_SHOW || e.OCP_SHOW || e.HANDLINE_FOLLOW || e.OCP_FOLLOW);
