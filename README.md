Crabstero is a simple Discord bot which generates sentences based on how users talk. It holds a different "personality" in each text channel, making it unique even if it is used across different servers. The sentences it generates are usually nonsense, making it a great bot to have nonversations with.

# Setup and Usage

To set up Crabstero in your server, use the invite link above. Upon arrival, it will automatically start learning from new messages and will be ready to use.

To use Crabstero, simply mention it anywhere in a message. Upon being mentioned, Crabstero will respond with a new sentence.

***Warning: Every word and image Crabstero outputs is sourced from other users. As a result, Crabstero may output content which is considered rude or inappropriate. Server moderators should moderate Crabstero like a regular user.***

# Permissions

Crabstero uses the following permissions for the following purposes:

* **View Channels** - Used to learn from new messages and detect mentions.
* **Send Messages** - Used to respond to mentions with new sentences.
* **Embed Links** - Used to occasionally attach embeds to responses.
* **Read Message History** - Used to learn from existing messages when invited to a new server.
* **Use External Emojis** - Allows Crabstero to use external emojis it learned from other users.
* **Add Reactions** - Currently unused, however this permission may be used in the future.

***Warning: Do not give Crabstero any privileged permissions, such as Administrator. It is not a moderation bot and will never have any moderation features. When inviting bots to your server, always follow the principle of least privilege to protect your server against compromised or rogue bots.***

# Configuration

Crabstero has no commands or settings. Instead, Crabstero is configured using Discord's permissions system. Reference the permissions list above to tune Crabstero's behavior.

*Example: To make Crabstero not respond to mentions in a certain channel, create a permission override in the channel which denies Crabstero's permission to create messages.*

# Privacy

When Crabstero generates a new sentence, it will only use information obtained from the same channel is it being mentioned in. Words are never shared between any text channels, even if they are in the same server.

When Crabstero learns from a message, only the message's content, its embeds, and the text channel's ID number is stored. No additional information is stored, including any information about the author.