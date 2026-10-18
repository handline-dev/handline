// The few line icons the agent page draws, one stroke weight, in the colour of the text beside them.
import Svg, { Path } from 'react-native-svg';

const PATHS = {
  search: 'M11 4a7 7 0 1 0 0 14a7 7 0 0 0 0-14zM21 21l-5-5',
  down: 'M6 9l6 6 6-6',
  up: 'M6 15l6-6 6 6',
  task: 'M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zM8 12l3 3 5-6',
  session: 'M3 12a9 9 0 1 0 3-6.7M3 4v5h5',
  tool: 'M4 17l6-6-6-6M12 19h8',
} as const;

export function Icon(props: { name: keyof typeof PATHS; color: string; size?: number }) {
  const s = props.size ?? 14;
  return (
    <Svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke={props.color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <Path d={PATHS[props.name]} />
    </Svg>
  );
}
