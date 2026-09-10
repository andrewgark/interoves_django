"""Field name lists for Direct .get methods."""

CAMPAIGN_FIELDS = [
    "Id",
    "Name",
    "Type",
    "Status",
    "State",
    "StatusPayment",
    "StatusClarification",
    "SourceId",
    "StartDate",
    "EndDate",
    "DailyBudget",
    "Currency",
    "Funds",
    "Statistics",
    "TimeZone",
    "NegativeKeywords",
    "ExcludedSites",
    "BlockedIps",
]

UNIFIED_CAMPAIGN_FIELDS = [
    "BiddingStrategy",
    "Settings",
    "CounterIds",
    "PriorityGoals",
    "TrackingParams",
    "AttributionModel",
    "NegativeKeywordSharedSetIds",
    "PackageBiddingStrategy",
]

ADGROUP_FIELDS = [
    "Id",
    "Name",
    "CampaignId",
    "RegionIds",
    "NegativeKeywords",
    "Type",
    "Status",
    "ServingStatus",
    "TrackingParams",
]

UNIFIED_ADGROUP_FIELDS = ["OfferRetargeting"]

AD_FIELDS = [
    "Id",
    "AdGroupId",
    "CampaignId",
    "Type",
    "Status",
    "State",
    "StatusClarification",
]

TEXT_AD_FIELDS = [
    "Title",
    "Title2",
    "Text",
    "Href",
    "Mobile",
    "DisplayUrlPath",
    "AdImageHash",
    "SitelinkSetId",
]

KEYWORD_FIELDS = [
    "Id",
    "Keyword",
    "AdGroupId",
    "CampaignId",
    "State",
    "Status",
    "ServingStatus",
    "StrategyPriority",
]

KEYWORD_AUTOTARGETING_FIELDS = [
    "Categories",
    "BrandOptions",
]

BIDMODIFIER_FIELDS = ["Id", "CampaignId", "AdGroupId", "Level", "Type"]
BIDMODIFIER_MOBILE_FIELDS = ["BidModifier", "OperatingSystemType"]
BIDMODIFIER_TABLET_FIELDS = ["BidModifier", "OperatingSystemType"]
