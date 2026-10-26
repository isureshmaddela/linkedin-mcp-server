# Social

Aira's own social network and messenger: a profile, friends and followers, a feed with photos and stories,
private and group chats, groups, pages for businesses and a work profile, all in one simple app that is safe for
every age. Unlike your household's Family space, Social connects you with people outside your home: everyone on
this computer (every Aira account) or, in the hosted product, members of every organization that has Social.

Open it from the apps menu. The menu badge counts your unread messages and notifications; a new message also
pops up as a reminder (read aloud in easy mode). Today shows **From your friends**: chats with new messages and
friend requests, with an **Accept** button.

## Getting started

* The first time, choose your **@name** (Aira suggests one), your display name, a picture (an emoji or an
  https:// photo link) and a few words about you.
* Choose who can see your profile: **Everyone** or **Only my friends**. Change it any time under Profile.

## Friends

* Search by name or @name. **Add friend** sends a request; the other person accepts or declines it.
* **Follow** someone with a public profile to see their public posts without being friends.
* **People you may know**: friends of your friends and people from your household (or organization).
* **Block** someone and they cannot see your profile or posts, find you, message you or send a request. You stop
  being friends. Blocking works both ways and across organizations. Unblock them under Friends.

## Feed

* Write a post (up to 2,000 characters) with up to six photo links, for **Everyone**, **Friends** or **Only me**.
* React with ❤️ 👍 😂 😮 😢 🎉 (tap again to take it back), comment, and share public posts.
* `#tags` link to every post with that tag you may see; `@name` mentions notify that person.
* Edit or delete your own posts and comments; the author of a post may delete comments on it.
* **Stories** last 24 hours, for your friends. You see who has seen yours.
* The feed updates by itself while it is open.

## Chats

* Message a friend, or anyone who chose "Let people who are not my friends message me" (adults only).
* Group chats: name the chat and tick your friends. The chat's admin adds and removes people; when the last
  admin leaves, the longest member takes over.
* Messages arrive live (every two seconds). Under your messages you see **Sent** or **Seen**.
* Send a photo link; delete a message for everyone within an hour of sending it.
* **Mute** a chat to stop its reminders and badge; **Hide** a one-to-one chat until the next message.
* On a phone the list and the conversation are separate screens; on a wide screen they are side by side.

## Groups and pages

* **Groups** are public (anyone joins) or private (people ask, an admin lets them in). Members post in the
  group; a private group's posts are only for its members.
* **Pages** are for shops, clubs and services (adults only). Follow a page to see its posts in your feed. A page
  can link to the business's own Aira public page (https).

## Profile and work

* Your profile shows your posts, friends and followers. A private profile shows strangers only your name.
* **Work profile**: a headline, open to work, skills and experience (one job per line: Title | Company | Start |
  End).
* Adults with a public profile can **share** it as a public page anyone can open without Aira: name, about,
  work and public posts. Turn the link off at any time.

## Safety for every age

* **Children** are private, cannot be found by strangers (only by friends and their own household), receive
  messages only from friends, never post for everyone, cannot share their profile, make groups or pages, and
  join only private groups an admin approves.
* **Teens** are private and receive messages only from friends.
* **Report** any post, comment, message, story or person. Links and photos must be https; posts are plain text
  (no HTML). Words on the blocked list are replaced with stars. Posting, commenting, messaging, friend requests
  and reports are limited per hour.

## Moderation

* On this computer the household's owner sees **Moderation**: reports filed by their household or about their
  household's people. **Hide it** hides the post, comment, message or story for everyone; **Suspend the person**
  (only someone in their household) hides everything they wrote and stops them posting; **Nothing wrong**
  closes the report. The owner also edits the blocked words.
* In the hosted product moderation belongs to Aira's platform operators (an organization's owner cannot hide
  another organization's content): **Social moderation** in the platform console (support and platform admins,
  `platform.moderation.manage`) lists open, resolved and dismissed reports with a preview, hides, suspends or
  reinstates, dismisses, and edits the blocked words (masked with asterisks). Every action is in the platform
  audit log (`social.moderate`, `social.suspend`, `social.unsuspend`, `social.blocklist`).

## Where the data lives

Social has one database for the whole server (`social.sqlite3`): next to the accounts database on this
computer, in the data folder of the hosted product. People are identified by their household profile (this
computer) or their organization membership (hosted), never by email address.

Because the file sits outside every organization's database, the hosted product handles it explicitly: a
person's own export carries `social.json` (their profile, posts, comments, messages they wrote and friends,
never other people's words); removing a member erases their Social account and everything they wrote;
deleting an organization erases all of its members' accounts. Groups, pages and chats they made stay for
the other people in them. Tests point Social at a scratch file (`tests/conftest.py`), never the real one.

