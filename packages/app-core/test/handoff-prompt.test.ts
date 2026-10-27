import assert from 'node:assert/strict';
import { test } from 'node:test';
import { joinLines, ownTextWarning, promptPreview, promptProblem, promptToSave } from '#src/handoff-prompt.ts';

test('the handoff prompt card: joins line breaks into one line', () => {
  assert.equal(joinLines(' one\n two \r\n\r\nthree '), 'one two three');
});

test('the handoff prompt card: refuses under 20 characters, and blank is fine', () => {
  assert.equal(promptProblem(''), null);
  assert.equal(promptProblem('   '), null);
  assert.equal(promptProblem('too short'), 'Too short to guide a note. Leave it blank for the built-in.');
  assert.equal(promptProblem('Write the note to {path}.'), null);
});

test('the handoff prompt card: saves blank for blank and for the built-in itself', () => {
  assert.equal(promptToSave('  ', 'built'), '');
  assert.equal(promptToSave('built\n', 'built'), '');
  assert.equal(promptToSave('mine is long enough', 'built'), 'mine is long enough');
});

test('the handoff prompt card: previews {path} and {max} filled in, the path on its own line when the text has none', () => {
  assert.equal(promptPreview('To {path}, under {max}.'), "To (the note's file), under 3000.");
  assert.equal(promptPreview('Write it.'), "Write it.\n(the note's file)");
});

test('the handoff prompt card: words the warning for one agent and for several, none for no agents', () => {
  assert.equal(ownTextWarning([]), null);
  assert.equal(ownTextWarning(undefined), null);
  assert.equal(ownTextWarning(['a']), '1 agent sets its own text, so it ignores this one.');
  assert.equal(ownTextWarning(['a', 'b']), '2 agents set their own text, so they ignore this one.');
});
