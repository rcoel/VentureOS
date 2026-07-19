You are parsing the Devpost hackathons index page.

You will receive raw text extracted from a page listing many Devpost
hackathons (each entry has a name, dates, and a link to the hackathon's
landing page). The URL filter targeted ENDED, Devpost-managed hackathons,
so most entries here should be ended.

Your job: emit a **HackathonList** with each hackathon's name and URL.

Rules:
1. Each hackathon's URL will be a subdomain of devpost.com, e.g.
   `https://gitlab.devpost.com/`, or a specific hackathon page like
   `https://foo-hackathon.devpost.com/`. Extract these full URLs.
2. Set `status`:
   - `"ended"` if the entry clearly shows the hackathon has finished
     (past dates, "Winners announced", or the page filter was for ended)
   - `"in_progress"` for currently running
   - `"upcoming"` for future
   - `"unknown"` if you can't tell
3. Skip any entry that doesn't have both a name AND a valid URL.
4. Do NOT invent URLs. If a hackathon name is mentioned but no URL is
   present in the text, skip it.
5. Cap at ~15 hackathons per page.

Output must conform exactly to the HackathonList schema.