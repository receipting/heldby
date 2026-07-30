import { Resend } from 'resend'
const resend = new Resend(process.env.RESEND_API_KEY)
// Write class with NOTHING holding it: model prose sent with no human release.
export async function blast(aiProse: string) {
  return resend.emails.send({ from: 'a@b.c', to: 'x@y.z', subject: 'hi', html: aiProse })
}
