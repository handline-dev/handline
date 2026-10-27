// A run in the background never comes to the front: no dock icon, no focus, no window over the person's, no native notices, and it does
// not wake the everyday app. An agent's run is one (its shell has OCP_AGENT) unless HANDLINE_BACKGROUND=0; HANDLINE_BACKGROUND=1 asks for it.
export const inBackground = (env: NodeJS.ProcessEnv): boolean =>
  env.HANDLINE_BACKGROUND === '1' || (env.HANDLINE_BACKGROUND !== '0' && !!env.OCP_AGENT);

/** Whether the run draws a menu bar icon: the everyday app does (packaged, or the follow-main checkout); a background or a dev run from a checkout does not, so a leftover one never shows as a second Handline. HANDLINE_TRAY=1 asks for the icon. */
export const drawsTray = (env: NodeJS.ProcessEnv, packaged: boolean): boolean =>
  env.HANDLINE_TRAY === '1' || (!inBackground(env) && (packaged || !!(env.HANDLINE_FOLLOW ?? env.OCP_FOLLOW)));
