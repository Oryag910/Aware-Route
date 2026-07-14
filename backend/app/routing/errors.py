class RouteNotFoundError(Exception):
    """The requested route could not be generated."""


class RoutingProviderError(Exception):
    """The routing provider failed or returned an invalid response."""
