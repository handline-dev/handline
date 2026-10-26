// A run in the background never comes to the front: no dock icon, no focus, no window over the person's, no native notices, and it does
// not wake the everyday app. An agent's run is one (its shell has OCP_AGENT) unless HANDLINE_BACKGROUND=0; HANDLINE_BACKGROUND=1 asks for it.
export const inBackground = (env: NodeJS.ProcessEnv): boolean =>
  env.HANDLINE_BACKGROUND === '1' || (env.HANDLINE_BACKGROUND !== '0' && !!env.OCP_AGENT);
