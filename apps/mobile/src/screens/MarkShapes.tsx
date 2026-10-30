// The marks drawn over a picture: the shapes as one SVG in image pixels, the dashed path of a drag in progress, and a numbered dot at each mark.
// MarkPicture (a draft) and PhotoViewer (a sent photo) lay this over their picture.
import { Text, View } from 'react-native';
import Svg, { Path, Polyline } from 'react-native-svg';
import { MARK_COLOR, marksToShapes, shapesToSvg, type Mark } from '@handline/app-core';
import { font } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';

export function MarkShapes(props: {
  marks: Mark[];
  /** The kept marks only, for the picture that is drawn into the file. */
  capturing: boolean;
  /** The pointer's path of a drag, in image pixels. */
  draft?: { x: number; y: number }[] | null;
  natural: { w: number; h: number };
  shown: { w: number; h: number };
  /** Image pixels to a screen point. */
  k: number;
  /** The width of a line in image pixels. */
  line: number;
  onOpen: (id: number) => void;
}) {
  const c = usePalette();
  const { marks, capturing, natural, shown, k } = props;
  const kept = marks.filter((m) => m.text.trim());
  const drawn = capturing ? kept : marks;
  const draw = shapesToSvg(marksToShapes(drawn, props.line, !capturing), natural.w || 1, natural.h || 1);
  const draft = props.draft?.map((p) => `${p.x},${p.y}`).join(' ');
  return (
    <View pointerEvents="box-none" style={{ position: 'absolute', left: 0, top: 0, width: shown.w, height: shown.h }}>
      <Svg width={shown.w} height={shown.h} viewBox={draw.viewBox} pointerEvents="none">
        {draw.elements.map((el, i) =>
          el.type === 'path' ? (
            <Path
              key={i}
              d={el.d}
              fill="none"
              stroke={el.stroke}
              strokeWidth={el.strokeWidth}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ) : null,
        )}
        {draft ? (
          <Polyline
            points={draft}
            fill="none"
            stroke={MARK_COLOR}
            strokeWidth={props.line}
            strokeDasharray={`${props.line * 2} ${props.line * 2}`}
          />
        ) : null}
      </Svg>
      {drawn.map((m, i) => (
        <View
          key={m.id}
          testID={`mark-${m.id}`}
          accessibilityLabel={`Comment ${i + 1}`}
          onTouchEnd={() => !capturing && props.onOpen(m.id)}
          style={{
            position: 'absolute',
            left: m.rect.x / k,
            top: m.rect.y / k,
            width: 22,
            height: 22,
            marginLeft: -11,
            marginTop: -11,
            borderRadius: 11,
            backgroundColor: MARK_COLOR,
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Text style={{ color: c.onFill, fontSize: font.size.sm, fontWeight: '700' }}>{i + 1}</Text>
        </View>
      ))}
    </View>
  );
}
