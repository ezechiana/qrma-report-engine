-- Apply the approved policy to existing Free plan settings once.
UPDATE app_settings
SET value = jsonb_set(value, '{free_features,sell_shares}', 'true'::jsonb)
WHERE key = 'subscription_config'
  AND value #> '{free_features,sell_shares}' = 'false'::jsonb;
