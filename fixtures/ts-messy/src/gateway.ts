import Anthropic from '@anthropic-ai/sdk'
// Read class: extraction checked against the real file.
const GATEWAY = 'https://gateway.ai.cloudflare.com/v1/acct/proj/anthropic'
export const PROCESS_TAG: string = 'invoice-extraction'
export async function extract(body: unknown) {
  const res = await fetch(`${GATEWAY}/v1/messages`, { method: 'POST', body: JSON.stringify(body) })
  return res.json()
}
export const client = new Anthropic({ baseURL: GATEWAY })
