import { WebClient } from '@slack/web-api'
import nodemailer from 'nodemailer'

const web = new WebClient(process.env.SLACK_TOKEN)
export const PROCESS_TAG: string = 'contact-extraction'

// alter-recipients, audience: the model picks who joins the channel, and every
// later message in it reaches them. Nothing sends here.
export async function widen(channel: string, extracted: string[]) {
  return web.conversations.invite({ channel, users: extracted.join(',') })
}

// alter-recipients, header: the address set is rewritten as its own statement.
export async function mail(contact: { email: string }, body: string) {
  const options: Record<string, unknown> = { from: 'a@b.c', subject: 'hi', text: body }
  options.to = contact.email
  options.bcc = ['archive@b.c']
  return nodemailer.createTransport({}).sendMail(options)
}
