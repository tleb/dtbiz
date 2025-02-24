# dtbiz

Visualise flattened devicetree
([DTB](https://devicetree-specification.readthedocs.io/en/stable/flattened-format.html)) files.

---

There are two versions:

 - Web, all in JS, without any back-end. **[Accessible online here](https://tleb.fr/dtbiz/).**

 - Python. No dependency either, it should run fine with a stock Python 3.

   ```
   ⟩ ./dtbiz.py demo.dtb > py.html
   ⟩ firefox py.html
   ```

---

![screenshot of the tool, showing a tree structure of the DTB](/screenshot.png)
