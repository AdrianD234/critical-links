# AMDS source discovery summary

Discovered at: 2026-07-27T03:36:16.977Z

## Item chain

| item id | type | title |
| --- | --- | --- |
| `c720e30739154520bc7d7c0fbfb2b6e5` | Web Experience | AMDS Network Model Application |
| `c8344c3898064bcda655b572187bf86b` | ERROR | ArcGIS error 403: You do not have permissions to access this resource or perform this operation. |
| `e6daee49bcff49f9901f45a8ff25fcf6` | Web Map | AMDS Network Model Webmap |
| `a1a59e23713d4807bb03de4ea808c5f6` | Image | Shared Theme Logo - TA |
| `dfa1e050f3cd42828f87d67fcff5a4fb` | Feature Service | AMDS Network Model Secured PROD |
| `98a6ed20c3b0416aacef36f77bdcd0ff` | Feature Service | AMDS Network Model Public Transport |
| `d284729222d04a3cb548cfe27716ea43` | Map Service | NZ Imagery |

## Feature service

- URL: https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services/AMDS_NetworkModel_PROD/FeatureServer
- Item id: `f955c118272b462e9ce757405890b87f`
- Owner: Publisher_NZTA
- Capabilities: `Query,Extract`
- maxRecordCount: 2000
- Licence info: Terms for public access to Network Model Disclaimer New Zealand Transport Agency Waka Kotahi ( NZTA ) does not accept any responsibility or liability (including any direct, special, indirect, incidental, consequential or other losses, costs, expenses, or damages) whatsoever, whether under statute or in contract, tort (including negligence), equity or otherwise, for the network model, nor for any use of, inability to use, or any reliance on, the network model. References to "network model" include this network model and all data, information and other content included in the network model. NZTA offers the network model as-is and as-available and makes no representations or warranties of any kind concerning the network model, whether express, implied, statutory, or otherwise. This includes, without limitation, warranties of title, merchantability, fitness for a particular purpose, non-infringement, absence of latent or other defects, accuracy, completeness, or the presence or absence of errors, whether or not known or discoverable. Users of the network model should apply, and rely upon, their own skill and judgement. NZTA has sole discretion over how it operates and provides the network model and, without notice, may close or replace the network model (in whole or in part), change the network model in any way (including by adding, changing and/or removing data, information, content, features or functionality in the network model) or suspend, limit and/or prevent access to and use of the network model (in whole or in part). Copyright information Copyright ©. This copyright work is licensed under the Creative Commons CC BY 4.0 International license. In essence, you are free to copy, distribute and adapt the work, as long as you attribute the work to NZTA and abide by the other license terms. To view a copy of this license, visit https://creativecommons.org/licenses/by/4.0/ . Other terms Our website disclaimer and terms of use apply to your use of our websites, including this website. Contact information Organisation: New Zealand Transport Agency Waka Kotahi Contact: amdsnetworkmodel@nzta.govt.nz
- Access information: New Zealand Transport Agency Waka Kotahi, KiwiRail, Land Information New Zealand and Controlling Authorities.

## Layers and tables

| id | name | geometry | features | fields |
| --- | --- | --- | --- | --- |
| 0 | AssetManagementDataStandard_LinearRefSysCalibration | esriGeometryPoint | 300504 | 16 |
| 1 | AssetManagementDataStandard_NetworkModel | esriGeometryPolyline | 677024 | 32 |
| 2 | AssetManagementDataStandard_Authority | table | 72 | 23 |
| 3 | AssetManagementDataStandard_Cycleway | table | 4748 | 20 |
| 4 | AssetManagementDataStandard_Geometry | table | 666484 | 26 |
| 5 | AssetManagementDataStandard_Lane | table | 227591 | 26 |
| 6 | AssetManagementDataStandard_LinearRefSysNetwork | table | 70 | 13 |
| 7 | AssetManagementDataStandard_LinearRefSysRoute | table | 91588 | 23 |
| 8 | AssetManagementDataStandard_LinearRefSysSequence | table | 203262 | 17 |
| 9 | AssetManagementDataStandard_RestrictedTurn | table | 60 | 40 |
| 10 | AssetManagementDataStandard_Restriction | table | 1372 | 23 |
| 11 | AssetManagementDataStandard_RouteName | table | 92065 | 40 |
| 12 | AssetManagementDataStandard_UrbanRural | table | 212416 | 19 |
| 13 | AMDS_NetworkModel_AMDS_RouteName | table | 98266 | 13 |

## Routable subset profile

| where | count |
| --- | --- |
| `1=1` | 677024 |
| `status=1` | 676880 |
| `status=1 AND modeVehicle=1` | 272441 |
| `status=1 AND modeVehicle=1 AND modelAssetType=1` | 262057 |
| `status=1 AND modeVehicle=1 AND oneway=1` | 9054 |
| `status=1 AND modeVehicle=1 AND oneway IS NULL` | 0 |
| `status=1 AND modeVehicleHeavy=1` | 271342 |
| `status=1 AND modeEmergencyManagement=1` | 248744 |
| `status=1 AND modeFerry=1` | 59 |
| `status=1 AND assetOwnerOrganisation=1` | 7303 |

## Extraction probe

```json
{
  "capabilities": "Query,Extract",
  "declaresExtract": true,
  "returnIdsOnlySupported": true,
  "idListSampleSize": 7123,
  "nativeOutSR2193": true
}
```
