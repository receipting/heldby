// A direct provider call that never touches the gateway. This is the bypass.
export async function sneaky(prompt: string) {
  return fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    body: JSON.stringify({ model: 'gpt-4o', messages: [{ role: 'user', content: prompt }] }),
  })
}
