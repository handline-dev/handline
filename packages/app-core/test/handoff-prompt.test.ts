import { describe, expect, it } from 'vitest';
import { joinLines, ownTextWarning, promptPreview, promptProblem, promptToSave } from '#src/handoff-prompt.ts';

describe('the handoff prompt card', () => {
  it('joins line breaks into one line', () => {
    expect(joinLines(' one\n two \r\n\r\nthree ')).toBe('one two three');
  });

  it('refuses under 20 characters, and blank is fine', () => {
    expect(promptProblem('')).toBeNull();
    expect(promptProblem('   ')).toBeNull();
    expect(promptProblem('too short')).toBe('Too short to guide a note. Leave it blank for the built-in.');
    expect(promptProblem('Write the note to {path}.')).toBeNull();
  });

  it('saves blank for blank and for the built-in itself', () => {
    expect(promptToSave('  ', 'built')).toBe('');
    expect(promptToSave('built\n', 'built')).toBe('');
    expect(promptToSave('mine is long enough', 'built')).toBe('mine is long enough');
  });

  it('previews {path} and {max} filled in, the path on its own line when the text has none', () => {
    expect(promptPreview('To {path}, under {max}.')).toBe("To (the note's file), under 3000.");
    expect(promptPreview('Write it.')).toBe("Write it.\n(the note's file)");
  });

  it('words the warning for one agent and for several, none for no agents', () => {
    expect(ownTextWarning([])).toBeNull();
    expect(ownTextWarning(undefined)).toBeNull();
    expect(ownTextWarning(['a'])).toBe('1 agent sets its own text, so it ignores this one.');
    expect(ownTextWarning(['a', 'b'])).toBe('2 agents set their own text, so they ignore this one.');
  });
});
