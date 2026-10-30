// The app's one icon set, the same lucide glyphs and names as the PC app's ui/Icon.tsx, drawn at phone size in the colour of the text beside them.
// The label is the accessibility label; a state is never told by colour alone, so each state has its own shape.
import { View } from 'react-native';
import type { LucideIcon } from 'lucide-react-native';
import * as Lucide from 'lucide-react-native';
import { ICON_SPEC, iconLabel, type GlyphName, type IconName } from '@handline/app-ui';

export { iconLabel };
export type { IconName };

const glyphOf = (name: IconName): LucideIcon => Lucide[ICON_SPEC[name][0] as GlyphName] as LucideIcon;

/** One icon; `label` overrides the default accessibility label, and a `decorative` icon is hidden from the reader (the text beside it says it). */
export function Icon(props: { name: IconName; color: string; size?: number; label?: string; decorative?: boolean; testID?: string }) {
  const Glyph = glyphOf(props.name);
  const text = ICON_SPEC[props.name][1];
  const size = props.size ?? 16;
  const glyph = <Glyph size={size} color={props.color} strokeWidth={2} />;
  if (props.decorative)
    return (
      <View testID={props.testID} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
        {glyph}
      </View>
    );
  return (
    <View testID={props.testID} accessible accessibilityRole="image" accessibilityLabel={props.label ?? text}>
      {glyph}
    </View>
  );
}
