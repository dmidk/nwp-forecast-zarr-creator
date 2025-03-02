#!/usr/bin/env python
# coding: utf-8

# In[ ]:


import eccodes
import matplotlib.pyplot as plt

# import cf_xarray as cfxr
import xarray as xr

eccodes.codes_set_definitions_path("/usr/share/eccodes/definitions")


# In[ ]:


fp = "/home/ec2-user/tmp/fc2025030112__dmi_sf/dini/heightAboveGround.json"
ds = xr.open_zarr(f"reference::{fp}")
ds


# In[ ]:


ds.z.plot(col="time", col_wrap=4)
plt.savefig("test.png")


# In[ ]:
