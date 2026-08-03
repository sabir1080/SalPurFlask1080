# TradeFlow ERP v1.0.0 - UI & Theme Testing Checklist

## THEME SWITCHING & PERSISTENCE

### THEME-001: Default Dark Theme on First Load
- **Priority**: Critical
- **Steps**:
  1. Clear browser localStorage: `localStorage.clear()` in console
  2. Refresh page
  3. Observe theme on signin page
- **Expected**: Dark theme loads by default, theme switcher shows dark button as active
- **Pass/Fail**: ___

### THEME-002: Theme Persistence After Login
- **Priority**: Critical
- **Steps**:
  1. Login to app
  2. Switch to light theme (click sun icon)
  3. Logout
  4. Login again
- **Expected**: Light theme is remembered and loads on next login
- **Pass/Fail**: ___

### THEME-003: Dark Theme After Login
- **Priority**: Critical
- **Steps**:
  1. Clear localStorage again
  2. Login to app
  3. Verify dark theme loads by default
- **Expected**: Dark theme loads automatically
- **Pass/Fail**: ___

---

## SIGNIN PAGE - LIGHT THEME

### SIGNIN-LIGHT-001: Navbar Menu Items Visibility
- **Priority**: Critical
- **Steps**:
  1. Navigate to /signin
  2. Switch to light theme (click sun icon in top-right)
  3. Look at left sidebar
- **Expected**: Manual, About, Sign In links are clearly readable with dark text
- **Pass/Fail**: ___

### SIGNIN-LIGHT-002: Theme Switcher Visibility
- **Priority**: High
- **Steps**:
  1. Go to /signin in light theme
  2. Look at top-right corner
- **Expected**: Light (sun) and dark (moon) buttons visible and clickable
- **Pass/Fail**: ___

### SIGNIN-LIGHT-003: Form Elements Visibility
- **Priority**: Critical
- **Steps**:
  1. In light theme on signin page
  2. Check email input, password input, Sign In button
  3. Check "Forgot Password?" link
- **Expected**: All inputs have white background with dark text, button is blue with white text, link is readable
- **Pass/Fail**: ___

### SIGNIN-LIGHT-004: Left Panel Text
- **Priority**: High
- **Steps**:
  1. Light theme on signin page
  2. Read the left panel (TradeFlow title, features list)
- **Expected**: All text is clearly readable with good contrast
- **Pass/Fail**: ___

---

## SIGNIN PAGE - DARK THEME

### SIGNIN-DARK-001: Navbar Menu Items Visibility
- **Priority**: Critical
- **Steps**:
  1. Navigate to /signin
  2. Ensure dark theme is active (click moon icon if needed)
  3. Look at left sidebar
- **Expected**: Manual, About, Sign In links are clearly readable with light text on dark background
- **Pass/Fail**: ___

### SIGNIN-DARK-002: Form Styling
- **Priority**: Critical
- **Steps**:
  1. In dark theme on signin page
  2. Check email input, password input, Sign In button
  3. Try typing in inputs
- **Expected**: Dark backgrounds, light text, inputs are readable
- **Pass/Fail**: ___

### SIGNIN-DARK-003: Left Panel in Dark
- **Priority**: High
- **Steps**:
  1. Dark theme on signin page
  2. Read left panel text
- **Expected**: Title, features, checkmarks are all visible with proper contrast
- **Pass/Fail**: ___

---

## DASHBOARD - LIGHT THEME

### DASH-LIGHT-001: Sidebar Menu Items
- **Priority**: Critical
- **Steps**:
  1. Login and switch to light theme
  2. Check left sidebar: Dashboard, Parties, Inventory, POS, Transactions, Reports, Accounting, Admin, Manual, About
- **Expected**: All menu items readable with dark text on light background
- **Pass/Fail**: ___

### DASH-LIGHT-002: Top Navigation Bar
- **Priority**: High
- **Steps**:
  1. Light theme on dashboard
  2. Check navbar background, text color, hover effects
- **Expected**: Light grey background with dark text, hover state shows blue highlight
- **Pass/Fail**: ___

### DASH-LIGHT-003: Cards and Content
- **Priority**: High
- **Steps**:
  1. Light theme, scroll through dashboard content
  2. Check cards, tables, text boxes
- **Expected**: All content readable, no white-on-white or light-on-light issues
- **Pass/Fail**: ___

### DASH-LIGHT-004: Buttons Visibility
- **Priority**: High
- **Steps**:
  1. Light theme, check all buttons: primary (blue), secondary (grey), danger (red)
- **Expected**: All buttons clearly visible with proper contrast and readable text
- **Pass/Fail**: ___

---

## DASHBOARD - DARK THEME

### DASH-DARK-001: Sidebar Menu Items
- **Priority**: Critical
- **Steps**:
  1. Login and ensure dark theme is active
  2. Check left sidebar for all menu items
- **Expected**: Light text on dark background, all items readable
- **Pass/Fail**: ___

### DASH-DARK-002: Alerts Visibility
- **Priority**: Critical
- **Steps**:
  1. Dark theme on dashboard
  2. Scroll to any alert box (info, warning, danger, success)
  3. Read the text
- **Expected**: Alert text is clearly readable with proper contrast
- **Pass/Fail**: ___

### DASH-DARK-003: Table Data Readability
- **Priority**: Critical
- **Steps**:
  1. Dark theme, navigate to any data list (Sales, Purchases, Customers, Suppliers)
  2. Check table headers, row data, alternating row colors
- **Expected**: All text readable, proper contrast between rows
- **Pass/Fail**: ___

### DASH-DARK-004: Form Labels and Placeholders
- **Priority**: High
- **Steps**:
  1. Dark theme, open any form (New Sale, New Purchase, New Customer)
  2. Check form labels, input placeholders, helper text
- **Expected**: Labels light colored, placeholders visible, helper text readable
- **Pass/Fail**: ___

---

## TEXT SELECTION

### SELECT-001: Light Theme Text Selection
- **Priority**: Medium
- **Steps**:
  1. Light theme
  2. Select any text on page by clicking and dragging
- **Expected**: Selected text has blue background with white text (high contrast)
- **Pass/Fail**: ___

### SELECT-002: Dark Theme Text Selection
- **Priority**: Medium
- **Steps**:
  1. Dark theme
  2. Select any text on page
- **Expected**: Selected text has blue background with white text (clearly visible)
- **Pass/Fail**: ___

---

## FILE UPLOAD INPUTS

### FILE-001: Light Theme File Input
- **Priority**: High
- **Steps**:
  1. Light theme, go to /bulk_import
  2. Look at "Choose File" input
- **Expected**: "No file chosen" text is visible with dark text
- **Pass/Fail**: ___

### FILE-002: Dark Theme File Input
- **Priority**: High
- **Steps**:
  1. Dark theme, go to /bulk_import
  2. Look at "Choose File" input
- **Expected**: "No file chosen" text is visible with light text
- **Pass/Fail**: ___

### FILE-003: File Input Button
- **Priority**: Medium
- **Steps**:
  1. Both themes, on /bulk_import
  2. Hover over file input button
- **Expected**: Button is readable, text visible
- **Pass/Fail**: ___

---

## ACCOUNTS PAGE (CASH & BANK)

### ACCT-LIGHT-001: Table Light Rows
- **Priority**: High
- **Steps**:
  1. Light theme, go to /accounts
  2. Look at "Control" account rows (highlighted rows)
- **Expected**: Text is readable, proper contrast
- **Pass/Fail**: ___

### ACCT-DARK-001: Table Light Rows in Dark
- **Priority**: High
- **Steps**:
  1. Dark theme, go to /accounts
  2. Look at alternating row colors and control account rows
- **Expected**: All rows readable, light text on dark background
- **Pass/Fail**: ___

### ACCT-002: Info Alert Box
- **Priority**: High
- **Steps**:
  1. Both themes, on /accounts page
  2. Read the blue info box at top: "Balances are computed from..."
- **Expected**: Text is clearly readable, good contrast
- **Pass/Fail**: ___

---

## BUTTONS ACROSS APP

### BTN-LIGHT-001: Primary Buttons
- **Priority**: High
- **Steps**:
  1. Light theme, scroll through app and find blue "Primary" buttons
  2. Examples: "Save Sale", "Add Item", "Manage Accounts"
- **Expected**: Blue button with white text, clearly visible and clickable
- **Pass/Fail**: ___

### BTN-LIGHT-002: Secondary Buttons
- **Priority**: Medium
- **Steps**:
  1. Light theme, find grey secondary buttons
  2. Examples: "Dashboard", "Back", "Cancel"
- **Expected**: Grey button with readable text
- **Pass/Fail**: ___

### BTN-DARK-001: Buttons in Dark Theme
- **Priority**: High
- **Steps**:
  1. Dark theme, check primary and secondary buttons
- **Expected**: All buttons clearly visible with proper contrast
- **Pass/Fail**: ___

---

## BADGES & SMALL ELEMENTS

### BADGE-LIGHT-001: Badge Visibility
- **Priority**: Medium
- **Steps**:
  1. Light theme, navigate to data pages (Sales, Purchases)
  2. Look for status badges (Paid, Partial, Unpaid, Pending)
- **Expected**: All badges readable with good contrast
- **Pass/Fail**: ___

### BADGE-DARK-001: Badge Visibility in Dark
- **Priority**: Medium
- **Steps**:
  1. Dark theme, check same badges
- **Expected**: Badges have proper contrast and are readable
- **Pass/Fail**: ___

---

## NAVIGATION & DROPDOWNS

### NAV-LIGHT-001: Dropdown Menus
- **Priority**: High
- **Steps**:
  1. Light theme, click on "Parties" menu item (dropdown)
  2. Check dropdown items visibility
- **Expected**: Dropdown has light background, dark text, hover state shows blue
- **Pass/Fail**: ___

### NAV-DARK-001: Dropdown Menus in Dark
- **Priority**: High
- **Steps**:
  1. Dark theme, click on any dropdown menu
- **Expected**: Dropdown has dark background, light text, proper contrast
- **Pass/Fail**: ___

### NAV-002: Hover Effects
- **Priority**: Medium
- **Steps**:
  1. Both themes, hover over menu items
- **Expected**: Clear visual feedback (background/color change)
- **Pass/Fail**: ___

---

## FORMS & INPUTS

### FORM-LIGHT-001: Input Fields
- **Priority**: High
- **Steps**:
  1. Light theme, open any form (New Sale, New Customer)
  2. Check input fields: borders, text color, placeholder text
- **Expected**: White inputs with dark text, readable placeholder text
- **Pass/Fail**: ___

### FORM-DARK-001: Input Fields in Dark
- **Priority**: High
- **Steps**:
  1. Dark theme, open same forms
- **Expected**: Dark inputs with light text, readable placeholder
- **Pass/Fail**: ___

### FORM-002: Labels and Help Text
- **Priority**: Medium
- **Steps**:
  1. Both themes, check form labels and helper text below inputs
- **Expected**: Labels visible, helper text readable
- **Pass/Fail**: ___

---

## OVERALL THEME QUALITY

### OVERALL-LIGHT-001: No Harsh Whiteness
- **Priority**: High
- **Steps**:
  1. Light theme, look at main background
- **Expected**: Background is slightly grey/blue (#d6dce8), not pure white, comfortable to view
- **Pass/Fail**: ___

### OVERALL-DARK-001: Proper Dark Contrast
- **Priority**: High
- **Steps**:
  1. Dark theme, scroll through entire app
- **Expected**: All text readable, no eye strain, good contrast everywhere
- **Pass/Fail**: ___

### OVERALL-002: Theme Consistency
- **Priority**: Medium
- **Steps**:
  1. Switch between light and dark theme multiple times
  2. Check different pages (Dashboard, Sales, Purchases, Reports)
- **Expected**: Consistent styling across all pages in each theme
- **Pass/Fail**: ___

---

## TESTING SUMMARY

**Total Test Cases**: 45

**Passed**: ___ / 45

**Failed**: ___ / 45

**Blocked**: ___ / 45

**Notes**:
```
[Write any issues found here]
```

**Critical Issues Found**:
- [ ] None
- [ ] Visibility issues
- [ ] Contrast issues
- [ ] Theme switching issues
- [ ] Other: ___________

**Recommendation for v1.0.0**: 
- [ ] Ready for Release
- [ ] Minor issues - can proceed with fix list
- [ ] Blocking issues - must fix before release
