# Live Order Contract

This contract defines the pre-live order request structure and validation path.

## Object
- `LiveOrderRequest`
  - symbol
  - market
  - side
  - qty
  - order_type
  - price
  - tif
  - venue
  - reason
  - tags

## Flow
1. decision -> paper intents
2. paper intents -> live order requests (draft only)
3. submit precheck validates request
4. audit store records attempt/result
5. real submit remains disabled until explicit approval
