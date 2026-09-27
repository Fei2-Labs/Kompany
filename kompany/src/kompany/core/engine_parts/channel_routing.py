"""CEO-channel mixin assembled from focused engine components."""

from kompany.core.engine_parts.channel_conversation import ChannelConversationMixin
from kompany.core.engine_parts.channel_delegation import ChannelDelegationMixin
from kompany.core.engine_parts.channel_spend import ChannelSpendMixin


class ChannelRoutingMixin(
    ChannelConversationMixin,
    ChannelDelegationMixin,
    ChannelSpendMixin,
):
    """Preserve public channel-routing mixin while keeping files focused."""
